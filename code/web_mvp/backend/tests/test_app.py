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


def test_desktop_shell_readiness_reports_disabled_preflight_boundary():
    client = TestClient(create_app())

    response = client.get("/desktop/shell-readiness")

    assert response.status_code == 200
    payload = response.json()
    assert payload["desktop_readiness_mode"] == "preflight_only"
    assert payload["desktop_readiness_status"] == "blocked_before_desktop_shell"
    assert payload["desktop_shell_status"] == "not_started"
    assert payload["target_platform_status"] == "macos_first_windows_deferred"
    assert payload["audio_capture_status"] == "not_connected"
    assert payload["microphone_permission_status"] == "not_requested"
    assert payload["system_audio_permission_status"] == "not_requested"
    assert payload["asr_worker_status"] == "not_started"
    assert payload["llm_provider_status"] == "not_connected"
    assert payload["local_data_dir_status"] == "not_created"
    assert payload["packaging_status"] == "not_started"
    assert payload["desktop_readiness_phase_count"] == 8
    assert len(payload["desktop_readiness_phases"]) == 8
    assert "desktop_shell_not_selected" in payload["desktop_readiness_blockers"]
    assert "choose_desktop_shell_runtime" in payload["desktop_readiness_next_decisions"]
    assert payload["desktop_safe_to_capture_audio"] is False
    assert payload["desktop_safe_to_request_permissions"] is False
    assert payload["desktop_safe_to_start_asr_worker"] is False
    assert payload["desktop_safe_to_call_remote_asr"] is False
    assert payload["desktop_safe_to_call_llm"] is False
    assert payload["desktop_safe_to_write_audio_chunks"] is False


def test_desktop_shell_readiness_does_not_probe_audio_or_read_secrets(monkeypatch, tmp_path):
    leaked_markers = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "desktop_readiness",
    )
    client = TestClient(create_app())

    response = client.get("/desktop/shell-readiness")

    assert response.status_code == 200
    response_text = response.text
    for marker in leaked_markers:
        assert marker not in response_text
    assert response.json()["audio_capture_status"] == "not_connected"
    assert response.json()["microphone_permission_status"] == "not_requested"
    assert response.json()["desktop_safe_to_capture_audio"] is False


def test_desktop_shell_readiness_with_data_dir_does_not_create_local_storage(tmp_path):
    app = create_app(data_dir=tmp_path)

    assert not (tmp_path / "sessions").exists()
    assert not (tmp_path / "live_asr_sessions").exists()

    client = TestClient(app)
    response = client.get("/desktop/shell-readiness")

    assert response.status_code == 200
    assert response.json()["local_data_dir_status"] == "not_created"
    assert not (tmp_path / "sessions").exists()
    assert not (tmp_path / "live_asr_sessions").exists()


def test_desktop_runtime_boundary_reports_decision_preflight():
    client = TestClient(create_app())

    response = client.get("/desktop/runtime-boundary")

    assert response.status_code == 200
    payload = response.json()
    assert payload["desktop_runtime_mode"] == "decision_preflight_only"
    assert payload["desktop_runtime_boundary_status"] == "blocked_before_runtime_creation"
    assert payload["recommended_desktop_runtime"] == "tauri_first_electron_fallback"
    assert payload["desktop_runtime_decision_status"] == "recommended_not_created"
    assert payload["desktop_process_model_status"] == "planned_not_started"
    assert payload["ui_reuse_status"] == "web_mvp_static_assets_reusable"
    assert payload["core_isolation_status"] == "platform_independent"
    assert payload["native_bridge_status"] == "not_created"
    assert payload["asr_worker_process_model"] == "sidecar_worker_planned"
    assert payload["packaging_pipeline_status"] == "not_started"
    assert payload["macos_target_status"] == "apple_silicon_first"
    assert payload["windows_target_status"] == "deferred_adapter"
    assert payload["desktop_runtime_phase_count"] == 8
    assert len(payload["desktop_runtime_phases"]) == 8
    assert "desktop_runtime_not_created" in payload["desktop_runtime_blockers"]
    assert "create_tauri_shell_spike" in payload["desktop_runtime_next_decisions"]
    assert payload["desktop_runtime_safe_to_create_shell"] is False
    assert payload["desktop_runtime_safe_to_start_native_bridge"] is False
    assert payload["desktop_runtime_safe_to_spawn_worker"] is False
    assert payload["desktop_runtime_safe_to_package_installer"] is False
    assert payload["desktop_runtime_safe_to_request_permissions"] is False
    assert payload["desktop_runtime_safe_to_capture_audio"] is False
    assert payload["desktop_runtime_safe_to_call_remote_asr"] is False
    assert payload["desktop_runtime_safe_to_call_llm"] is False


def test_desktop_runtime_boundary_does_not_probe_audio_or_read_secrets(
    monkeypatch, tmp_path
):
    leaked_markers = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "desktop_runtime_boundary",
    )
    client = TestClient(create_app())

    response = client.get("/desktop/runtime-boundary")

    assert response.status_code == 200
    response_text = response.text
    for marker in leaked_markers:
        assert marker not in response_text
    payload = response.json()
    assert payload["native_bridge_status"] == "not_created"
    assert payload["desktop_runtime_safe_to_create_shell"] is False
    assert payload["desktop_runtime_safe_to_capture_audio"] is False


def test_desktop_runtime_boundary_with_data_dir_does_not_create_local_storage(
    tmp_path,
):
    app = create_app(data_dir=tmp_path)

    assert not (tmp_path / "sessions").exists()
    assert not (tmp_path / "live_asr_sessions").exists()

    client = TestClient(app)
    response = client.get("/desktop/runtime-boundary")

    assert response.status_code == 200
    assert (
        response.json()["desktop_runtime_boundary_status"]
        == "blocked_before_runtime_creation"
    )
    assert not (tmp_path / "sessions").exists()
    assert not (tmp_path / "live_asr_sessions").exists()


def test_desktop_native_bridge_contract_reports_contract_preflight():
    client = TestClient(create_app())

    response = client.get("/desktop/native-bridge-contract")

    assert response.status_code == 200
    payload = response.json()
    assert payload["desktop_bridge_contract_mode"] == "contract_preflight_only"
    assert payload["desktop_bridge_contract_status"] == "specified_not_bound"
    assert payload["native_bridge_status"] == "not_created"
    assert payload["desktop_shell_runtime_status"] == "not_created"
    assert payload["bridge_transport_status"] == "not_created"
    assert payload["bridge_command_contract_status"] == "specified_not_bound"
    assert payload["bridge_process_lifecycle_status"] == "specified_not_started"
    assert payload["bridge_resource_policy_status"] == "specified_not_enforced"
    assert payload["bridge_error_contract_status"] == "specified"
    assert payload["bridge_audit_contract_status"] == "response_only"
    assert payload["bridge_platform_adapter_status"] == "not_created"
    assert payload["desktop_bridge_command_count"] == 8
    assert len(payload["desktop_bridge_commands"]) == 8
    assert payload["desktop_bridge_phase_count"] == 8
    assert len(payload["desktop_bridge_phases"]) == 8
    command_ids = {
        command["command_id"] for command in payload["desktop_bridge_commands"]
    }
    assert "runtime.get_status" in command_ids
    assert "audio.capture_start" in command_ids
    assert "asr_worker.start" in command_ids
    for command in payload["desktop_bridge_commands"]:
        assert command["command_status"] == "contract_only"
        assert command["implementation_status"] == "not_bound"
        assert command["transport_status"] == "not_created"
        assert command["side_effect_status"] == "forbidden"
        assert command["safe_to_execute_now"] is False
        assert command["safe_to_invoke"] is False
        assert command["request_schema_status"] == "outline_only"
        assert command["response_schema_status"] == "outline_only"
        assert "effect_class" in command
        assert "requires_explicit_user_action" in command
        assert isinstance(command["requires_explicit_user_action"], bool)
        assert "read_set" in command
        assert isinstance(command["read_set"], list)
        assert "write_set" in command
        assert isinstance(command["write_set"], list)
        assert "spawns_process" in command
        assert isinstance(command["spawns_process"], bool)
        assert "captures_audio" in command
        assert isinstance(command["captures_audio"], bool)
        assert "calls_remote_provider" in command
        assert command["calls_remote_provider"] is False
        assert "future_adapter" in command
        assert "failure_mode" in command
        assert "security_classification" in command
    assert (
        payload["desktop_bridge_error_contract"]["secret_redaction_policy"]
        == "no_secret_values"
    )
    assert payload["desktop_bridge_resource_policy"]["worker_spawn_status"] == "not_started"
    assert "native_bridge_not_created" in payload["desktop_bridge_blockers"]
    assert (
        "create_tauri_shell_scaffold_against_bridge_contract"
        in payload["desktop_bridge_next_decisions"]
    )
    assert payload["desktop_bridge_safe_to_create_native_bridge"] is False
    assert payload["desktop_bridge_safe_to_bind_ipc"] is False
    assert payload["desktop_bridge_safe_to_invoke_commands"] is False
    assert payload["desktop_bridge_safe_to_request_permissions"] is False
    assert payload["desktop_bridge_safe_to_enumerate_devices"] is False
    assert payload["desktop_bridge_safe_to_capture_audio"] is False
    assert payload["desktop_bridge_safe_to_spawn_worker"] is False
    assert payload["desktop_bridge_safe_to_write_local_files"] is False
    assert payload["desktop_bridge_safe_to_call_remote_asr"] is False
    assert payload["desktop_bridge_safe_to_call_llm"] is False


def test_desktop_native_bridge_contract_does_not_probe_audio_or_read_secrets(
    monkeypatch, tmp_path
):
    leaked_markers = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "desktop_native_bridge_contract",
    )
    _install_no_native_audio_or_process_guards(
        monkeypatch,
        "desktop_native_bridge_contract",
    )
    client = TestClient(create_app())

    response = client.get("/desktop/native-bridge-contract")

    assert response.status_code == 200
    response_text = response.text
    for marker in leaked_markers:
        assert marker not in response_text
    payload = response.json()
    assert payload["native_bridge_status"] == "not_created"
    assert payload["desktop_bridge_safe_to_create_native_bridge"] is False
    assert payload["desktop_bridge_safe_to_spawn_worker"] is False
    assert payload["desktop_bridge_safe_to_capture_audio"] is False


def test_desktop_native_bridge_contract_with_data_dir_does_not_create_local_storage(
    tmp_path,
):
    app = create_app(data_dir=tmp_path)

    assert not (tmp_path / "sessions").exists()
    assert not (tmp_path / "live_asr_sessions").exists()

    client = TestClient(app)
    response = client.get("/desktop/native-bridge-contract")

    assert response.status_code == 200
    assert response.json()["desktop_bridge_contract_status"] == "specified_not_bound"
    assert not (tmp_path / "sessions").exists()
    assert not (tmp_path / "live_asr_sessions").exists()


def test_desktop_asr_worker_handoff_dry_run_readiness_reports_noop_boundary():
    client = TestClient(create_app())

    response = client.get("/desktop/asr-worker-handoff-dry-run-readiness")

    assert response.status_code == 200
    payload = response.json()
    assert payload["pcweb_id"] == "PCWEB-096"
    assert payload["next_pcweb_id"] == "PCWEB-106"
    assert payload["desktop_asr_worker_handoff_dry_run_mode"] == "readiness_only"
    assert payload["desktop_asr_worker_handoff_dry_run_status"] == "preview_only_ready"
    assert payload["pcweb_096_default_dry_run_status"] == "preview_ready_no_web_mutation"
    assert payload["synthetic_local_test_status"] == "explicit_mode_only"
    assert payload["worker_execution_status"] == "not_started"
    assert payload["event_file_read_status"] == "not_read"
    assert payload["web_handoff_mutation_status"] == "not_mutated"
    assert payload["handoff_api_endpoint"] == "/live/asr/local-event-files/sessions"
    assert payload["approved_event_file_root"] == "artifacts/tmp/asr_events"
    assert payload["approved_temp_web_data_dir_root"] == "artifacts/tmp/desktop_handoff_dry_run"
    assert payload["desktop_asr_handoff_phase_count"] == 7
    assert len(payload["desktop_asr_handoff_phases"]) == 7
    assert "asr_worker_not_started" in payload["desktop_asr_handoff_blockers"]
    assert "command_runner_binding_not_approved" in payload[
        "desktop_asr_handoff_blockers"
    ]
    assert "command_runner_implementation_skeleton_not_approved" in payload[
        "desktop_asr_handoff_blockers"
    ]
    assert "mic_adapter_not_bound_to_desktop_runtime" in payload[
        "desktop_asr_handoff_blockers"
    ]
    assert "surface_mic_adapter_contract_readiness_ui" in payload[
        "desktop_asr_handoff_next_decisions"
    ]
    assert payload["mic_adapter_contract_status"] == "specified_not_executable"
    assert payload["desktop_asr_handoff_safe_to_request_audio_permission"] is False
    assert payload["command_runner_binding_status"] == "not_bound"
    assert payload["command_runner_implementation_skeleton_status"] == "not_bound_no_dispatch"
    assert payload["command_runner_execution_status"] == "not_executed"
    assert payload["desktop_asr_handoff_safe_to_bind_command_runner"] is False
    assert payload["desktop_asr_handoff_safe_to_accept_worker_command"] is False
    assert payload["desktop_asr_handoff_safe_to_dispatch_worker_command"] is False
    assert payload["desktop_asr_handoff_safe_to_run_subprocess"] is False
    assert payload["desktop_asr_handoff_safe_to_invoke_tauri_ipc"] is False
    assert payload["desktop_asr_handoff_safe_to_start_worker"] is False
    assert payload["desktop_asr_handoff_safe_to_capture_audio"] is False
    assert payload["desktop_asr_handoff_safe_to_read_real_audio"] is False
    assert payload["desktop_asr_handoff_safe_to_read_configs_local"] is False
    assert payload["desktop_asr_handoff_safe_to_call_remote_asr"] is False
    assert payload["desktop_asr_handoff_safe_to_call_llm"] is False
    assert payload["desktop_asr_handoff_safe_to_download_models"] is False
    assert payload["desktop_asr_handoff_safe_to_run_tauri_or_cargo"] is False
    assert payload["desktop_asr_handoff_safe_to_mutate_web_session_now"] is False


def test_desktop_asr_worker_handoff_dry_run_readiness_does_not_probe_audio_or_read_secrets(
    monkeypatch, tmp_path
):
    leaked_markers = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "desktop_asr_handoff_dry_run_readiness",
    )
    _install_no_native_audio_or_process_guards(
        monkeypatch,
        "desktop_asr_handoff_dry_run_readiness",
    )
    client = TestClient(create_app())

    response = client.get("/desktop/asr-worker-handoff-dry-run-readiness")

    assert response.status_code == 200
    response_text = response.text
    for marker in leaked_markers:
        assert marker not in response_text
    payload = response.json()
    assert payload["desktop_asr_handoff_safe_to_start_worker"] is False
    assert payload["desktop_asr_handoff_safe_to_capture_audio"] is False
    assert payload["desktop_asr_handoff_safe_to_read_configs_local"] is False


def test_desktop_asr_worker_handoff_dry_run_readiness_with_data_dir_does_not_create_local_storage(
    tmp_path,
):
    app = create_app(data_dir=tmp_path)

    assert not (tmp_path / "sessions").exists()
    assert not (tmp_path / "live_asr_sessions").exists()
    assert not (tmp_path / "desktop_handoff_dry_run").exists()

    client = TestClient(app)
    response = client.get("/desktop/asr-worker-handoff-dry-run-readiness")

    assert response.status_code == 200
    assert response.json()["desktop_asr_worker_handoff_dry_run_status"] == "preview_only_ready"
    assert not (tmp_path / "sessions").exists()
    assert not (tmp_path / "live_asr_sessions").exists()
    assert not (tmp_path / "desktop_handoff_dry_run").exists()


def test_desktop_mic_adapter_contract_readiness_reports_contract_without_audio_access():
    client = TestClient(create_app())

    response = client.get("/desktop/mic-adapter-contract-readiness")

    assert response.status_code == 200
    payload = response.json()
    assert payload["pcweb_id"] == "PCWEB-106"
    assert payload["source_pcweb_id"] == "PCWEB-105"
    assert payload["readiness_mode"] == "readiness_only_no_mic_permission"
    assert payload["mic_adapter_ui_status"] == "ready_noop_contract_visible"
    assert payload["mic_adapter_contract_status"] == "specified_not_executable"
    assert payload["contract_version"] == "desktop_mic_adapter_contract.v1"
    assert payload["adapter_execution_status"] == "not_bound_not_executed"
    assert payload["permission_request_status"] == "not_requested"
    assert payload["audio_capture_status"] == "not_started"
    assert payload["audio_chunk_write_status"] == "not_written"
    assert payload["audio_chunk_delete_status"] == "not_executed"
    assert payload["user_start_boundary"] == "explicit_user_start_required_before_capture"
    assert payload["approved_runtime_audio_root"] == "artifacts/tmp/desktop_mic_adapter_runtime"
    assert payload["approved_audio_chunk_root"] == (
        "artifacts/tmp/desktop_mic_adapter_runtime/audio_chunks"
    )
    assert payload["delete_semantics"] == "delete_audio_chunks_before_session_discard"
    assert payload["mic_adapter_command_count"] == 7
    assert [item["command_id"] for item in payload["mic_adapter_command_catalog"]] == [
        "mic_adapter.prepare",
        "mic_adapter.status",
        "mic_adapter.start",
        "mic_adapter.pause",
        "mic_adapter.resume",
        "mic_adapter.stop",
        "mic_adapter.delete_audio_chunks",
    ]
    assert all(item["safe_to_execute_now"] is False for item in payload["mic_adapter_command_catalog"])
    assert "mic_adapter_not_bound_to_desktop_runtime" in payload["mic_adapter_readiness_blockers"]
    assert "audio_permission_not_requested" in payload["mic_adapter_readiness_blockers"]
    assert "real_capture_requires_future_explicit_user_start" in payload[
        "mic_adapter_readiness_blockers"
    ]
    assert "bind_mic_adapter_noop_ipc_after_tauri_approval" in payload[
        "mic_adapter_readiness_next_decisions"
    ]
    assert payload["safe_to_bind_mic_adapter_now"] is False
    assert payload["safe_to_request_audio_permission_now"] is False
    assert payload["safe_to_capture_audio_now"] is False
    assert payload["safe_to_write_audio_chunk_now"] is False
    assert payload["safe_to_delete_audio_chunks_now"] is False
    assert payload["safe_to_read_user_audio_now"] is False
    assert payload["safe_to_read_configs_local_now"] is False
    assert payload["safe_to_call_remote_asr_now"] is False
    assert payload["safe_to_call_llm_now"] is False
    assert payload["safe_to_run_tauri_or_cargo_now"] is False


def test_desktop_mic_adapter_contract_readiness_does_not_probe_audio_or_read_secrets(
    monkeypatch, tmp_path
):
    leaked_markers = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "desktop_mic_adapter_contract_readiness",
    )
    _install_no_native_audio_or_process_guards(
        monkeypatch,
        "desktop_mic_adapter_contract_readiness",
    )
    client = TestClient(create_app())

    response = client.get("/desktop/mic-adapter-contract-readiness")

    assert response.status_code == 200
    response_text = response.text
    for marker in leaked_markers:
        assert marker not in response_text
    payload = response.json()
    assert payload["safe_to_capture_audio_now"] is False
    assert payload["safe_to_request_audio_permission_now"] is False
    assert payload["safe_to_read_configs_local_now"] is False


def test_desktop_mic_adapter_contract_readiness_with_data_dir_does_not_create_local_storage(
    tmp_path,
):
    app = create_app(data_dir=tmp_path)

    assert not (tmp_path / "sessions").exists()
    assert not (tmp_path / "live_asr_sessions").exists()
    assert not (tmp_path / "desktop_mic_adapter_runtime").exists()

    client = TestClient(app)
    response = client.get("/desktop/mic-adapter-contract-readiness")

    assert response.status_code == 200
    assert response.json()["mic_adapter_ui_status"] == "ready_noop_contract_visible"
    assert not (tmp_path / "sessions").exists()
    assert not (tmp_path / "live_asr_sessions").exists()
    assert not (tmp_path / "desktop_mic_adapter_runtime").exists()


def test_desktop_real_mic_shadow_test_readiness_reports_static_gate_without_audio_access():
    client = TestClient(create_app())

    response = client.get("/desktop/real-mic-shadow-test-readiness")

    assert response.status_code == 200
    payload = response.json()
    assert payload["pcweb_id"] == "PCWEB-115"
    assert payload["readiness_mode"] == "static_preflight_report_only"
    assert payload["readiness_status"] == "blocked_not_ready_for_user_real_mic_shadow_test"
    assert payload["user_can_start_real_mic_shadow_test_now"] is False
    assert payload["asr_quality_exit_status"] == "not_exited"
    assert payload["asr_quality_counts_as_go_evidence"] is False
    assert payload["worker_mic_source_approval_status"] == "not_approved"
    assert payload["tauri_noop_evidence_status"] == "not_provided"
    assert payload["mic_adapter_implementation_status"] == "not_provided"
    assert payload["asr_worker_implementation_status"] == "not_provided"
    assert payload["export_feedback_status"] == "ready_for_real_report_after_user_shadow_test"
    assert "asr_quality_decision_requires_funasr_model_dir_or_drv019_approval" in payload[
        "blockers"
    ]
    assert "real_tauri_noop_run_result_not_provided" in payload["blockers"]
    assert "worker_mic_source_not_approved" in payload["blockers"]
    assert payload["pilot_protocol"]["user_start_required"] is True
    assert payload["pilot_protocol"]["raw_audio_upload_default"] is False
    assert payload["pilot_protocol"]["remote_asr_default"] is False
    assert payload["safe_to_access_microphone_from_gate_now"] is False
    assert payload["safe_to_request_audio_permission_from_gate_now"] is False
    assert payload["safe_to_spawn_worker_from_gate_now"] is False
    assert payload["safe_to_run_tauri_or_cargo_from_gate_now"] is False
    assert payload["safe_to_read_configs_local_from_gate_now"] is False
    assert payload["safe_to_call_remote_asr_from_gate_now"] is False
    assert payload["safe_to_call_llm_from_gate_now"] is False


def test_desktop_real_mic_shadow_test_readiness_does_not_probe_audio_or_read_secrets(
    monkeypatch, tmp_path
):
    leaked_markers = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "desktop_real_mic_shadow_test_readiness",
    )
    _install_no_native_audio_or_process_guards(
        monkeypatch,
        "desktop_real_mic_shadow_test_readiness",
    )
    client = TestClient(create_app())

    response = client.get("/desktop/real-mic-shadow-test-readiness")

    assert response.status_code == 200
    response_text = response.text
    for marker in leaked_markers:
        assert marker not in response_text
    payload = response.json()
    assert payload["user_can_start_real_mic_shadow_test_now"] is False
    assert payload["safe_to_access_microphone_from_gate_now"] is False
    assert payload["safe_to_read_configs_local_from_gate_now"] is False


def test_desktop_real_mic_shadow_test_readiness_with_data_dir_does_not_create_local_storage(
    tmp_path,
):
    app = create_app(data_dir=tmp_path)

    assert not (tmp_path / "sessions").exists()
    assert not (tmp_path / "live_asr_sessions").exists()
    assert not (tmp_path / "real_mic_shadow_reports").exists()

    client = TestClient(app)
    response = client.get("/desktop/real-mic-shadow-test-readiness")

    assert response.status_code == 200
    assert response.json()["readiness_status"] == "blocked_not_ready_for_user_real_mic_shadow_test"
    assert not (tmp_path / "sessions").exists()
    assert not (tmp_path / "live_asr_sessions").exists()
    assert not (tmp_path / "real_mic_shadow_reports").exists()


def test_local_shadow_preview_release_readiness_reports_truthful_status():
    client = TestClient(create_app())

    response = client.get("/desktop/local-shadow-preview-release-readiness")

    assert response.status_code == 200
    payload = response.json()
    assert payload["release_tier"] == "local_shadow_preview"
    assert payload["demo_preview_ready"] is True
    assert payload["shadow_pilot_ready"] is False
    assert payload["production_mvp_ready"] is False
    assert payload["asr_quality_exit_status"] == "not_exited"
    assert payload["asr_quality_decision_status"] == (
        "blocked_by_funasr_smoke_assembly_input_guard"
    )
    assert payload["real_mic_readiness_status"] == (
        "blocked_not_ready_for_user_real_mic_shadow_test"
    )
    assert payload["llm_execution_status"] == "disabled_not_called"
    assert payload["formal_card_status"] == "not_created_in_current_mainline_preview"
    assert payload["formal_report_status"] == "preview_only_not_real_meeting_go_evidence"
    assert payload["allowed_claim"] == "local synthetic/replay/artifact Copilot preview"
    assert "real meeting ready" not in payload["allowed_claim"].lower()
    assert payload["forbidden_claims"] == [
        "real meeting ready",
        "production ASR ready",
        "production MVP ready",
        "background microphone capture ready",
    ]
    assert payload["safety_flags"]["safe_to_capture_microphone_now"] is False
    assert payload["safety_flags"]["safe_to_capture_system_audio_now"] is False
    assert payload["safety_flags"]["safe_to_call_remote_asr_now"] is False
    assert payload["safety_flags"]["safe_to_call_llm_now"] is False
    assert payload["safety_flags"]["safe_to_read_configs_local_now"] is False


def test_desktop_tauri_noop_run_result_validation_accepts_collector_result_without_running_tauri():
    client = TestClient(create_app())

    response = client.post(
        "/desktop/tauri-noop-run-results/validations",
        json={"run_result": _valid_tauri_noop_run_result()},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["pcweb_id"] == "PCWEB-113"
    assert payload["result_validation_status"] == "passed"
    assert payload["tauri_noop_run_result_status"] == "validated_noop_ipc_observed"
    assert payload["real_tauri_noop_run_evidence_status"] == (
        "ready_for_worker_mic_source_approval_review"
    )
    assert payload["validated_command_count"] == 10
    assert payload["returned_command_count"] == 10
    assert payload["tauri_run_execution_status"] == "not_run_by_intake"
    assert payload["external_command_execution_status"] == "not_run"
    assert payload["safe_to_capture_audio_now"] is False
    assert payload["safe_to_start_asr_worker_now"] is False
    assert payload["safe_to_call_remote_asr_now"] is False
    assert payload["safe_to_call_llm_now"] is False


def test_desktop_tauri_noop_run_result_validation_rejects_browser_fallback_or_side_effects():
    client = TestClient(create_app())
    browser_fallback = _valid_tauri_noop_run_result()
    browser_fallback["run_environment"] = "browser_fallback"
    browser_fallback["explicit_tauri_run_approval_recorded"] = False
    side_effect = _valid_tauri_noop_run_result()
    side_effect["command_results"][3]["result"]["captures_audio"] = True

    fallback_response = client.post(
        "/desktop/tauri-noop-run-results/validations",
        json={"run_result": browser_fallback},
    )
    side_effect_response = client.post(
        "/desktop/tauri-noop-run-results/validations",
        json={"run_result": side_effect},
    )

    assert fallback_response.status_code == 422
    assert fallback_response.json()["detail"]["result_validation_status"] == "failed"
    assert "run_environment must be tauri_webview" in fallback_response.json()["detail"][
        "result_validation_errors"
    ]
    assert side_effect_response.status_code == 422
    assert "command mic_adapter.prepare captures_audio must be false" in (
        side_effect_response.json()["detail"]["result_validation_errors"]
    )


def test_desktop_tauri_noop_run_result_validation_with_data_dir_does_not_create_storage(tmp_path):
    app = create_app(data_dir=tmp_path)

    client = TestClient(app)
    response = client.post(
        "/desktop/tauri-noop-run-results/validations",
        json={"run_result": _valid_tauri_noop_run_result()},
    )

    assert response.status_code == 200
    assert not (tmp_path / "sessions").exists()
    assert not (tmp_path / "live_asr_sessions").exists()
    assert not (tmp_path / "desktop_tauri_noop_run_results").exists()


def test_desktop_mac_local_shadow_mvp_demo_session_creates_synthetic_live_asr_closure():
    client = TestClient(create_app())

    response = client.post(
        "/desktop/mac-local-shadow-mvp-demo/sessions",
        json={"session_id": "mac_shadow_mvp_review"},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["demo_id"] == "mac_local_shadow_mvp"
    assert payload["demo_status"] == "synthetic_demo_session_created"
    assert payload["session_id"] == "mac_shadow_mvp_review"
    assert payload["provider"] == "local_mock_asr"
    assert payload["execution_boundary"] == (
        "synthetic_events_only_no_mic_no_audio_file_no_remote_calls"
    )
    assert payload["closure_status"] == (
        "closed_to_no_llm_request_draft_and_readiness_blockers"
    )
    assert payload["product_chain"] == [
        "synthetic_streaming_events",
        "transcript_partial_preview",
        "transcript_final_revision",
        "evidence_span",
        "meeting_state",
        "suggestion_candidate",
        "llm_request_draft_no_call",
        "real_mic_readiness_blocked",
    ]
    live_event_counts = payload["live_event_counts"]
    assert live_event_counts["transcript_partial"] >= 1
    assert live_event_counts["transcript_final"] >= 3
    assert live_event_counts["transcript_revision"] >= 1
    assert live_event_counts["state_event"] >= 4
    assert live_event_counts["scheduler_event"] >= 4
    assert live_event_counts["suggestion_candidate_event"] >= 1
    assert live_event_counts["llm_request_draft_event"] >= 1
    assert live_event_counts["suggestion_card"] == 0
    assert payload["formal_card_creation_status"] == "not_created"
    assert payload["all_llm_statuses"] == ["not_called"]
    assert payload["llm_execution_status"] == "not_called"
    assert payload["remote_asr_call_status"] == "not_called"
    assert payload["real_mic_shadow_readiness_status"] == (
        "blocked_not_ready_for_user_real_mic_shadow_test"
    )
    assert payload["user_can_start_real_mic_shadow_test_now"] is False
    assert "asr_quality_gate_not_exited" in payload["readiness_blockers"]
    assert payload["safe_to_capture_microphone_now"] is False
    assert payload["safe_to_read_user_audio_now"] is False
    assert payload["safe_to_call_remote_asr_now"] is False
    assert payload["safe_to_call_llm_now"] is False
    assert payload["safe_to_download_models_now"] is False
    assert payload["safe_to_read_configs_local_now"] is False

    events_response = client.get("/live/asr/sessions/mac_shadow_mvp_review/events")
    drafts_response = client.get(
        "/live/asr/sessions/mac_shadow_mvp_review/llm-request-drafts"
    )

    assert events_response.status_code == 200
    assert drafts_response.status_code == 200
    assert events_response.json()["source"] == "live_asr_stream"
    assert len(events_response.json()["events"]) == len(payload["live_events"])
    assert drafts_response.json()["request_draft_count"] == live_event_counts[
        "llm_request_draft_event"
    ]


def test_desktop_realistic_meeting_simulation_pack_creates_richer_live_asr_session():
    client = TestClient(create_app())

    response = client.post(
        "/desktop/realistic-meeting-simulation-pack/sessions",
        json={"session_id": "realistic_sim_review"},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["simulation_id"] == "realistic_meeting_simulation_pack"
    assert payload["simulation_status"] == "realistic_synthetic_session_created"
    assert payload["scenario_id"] == "pcweb_126_release_incident_review"
    assert payload["session_id"] == "realistic_sim_review"
    assert payload["provider"] == "local_mock_asr"
    assert payload["execution_boundary"] == (
        "synthetic_realistic_events_only_no_mic_no_audio_file_no_remote_calls"
    )
    assert payload["meeting_shape"] == {
        "speaker_count": 4,
        "speaker_turn_count": 8,
        "duration_seconds": 47.2,
        "overlap_marker_count": 1,
        "pause_marker_count": 2,
        "revision_count": 2,
    }
    assert payload["realism_features"] == [
        "multi_speaker_turns",
        "partial_corrections",
        "revision_after_misheard_number",
        "pause_gap_markers",
        "overlap_marker",
        "technical_term_dense_release_incident_review",
        "no_remote_provider_no_audio_file",
    ]
    assert payload["technical_terms"] == [
        "payment-gateway",
        "P99",
        "0.1%",
        "Kafka lag",
        "rollback",
        "feature flag",
    ]
    live_event_counts = payload["live_event_counts"]
    assert live_event_counts["transcript_partial"] >= 3
    assert live_event_counts["transcript_final"] >= 6
    assert live_event_counts["transcript_revision"] >= 2
    assert live_event_counts["state_event"] >= 6
    assert live_event_counts["scheduler_event"] >= 6
    assert live_event_counts["suggestion_candidate_event"] >= 2
    assert live_event_counts["llm_request_draft_event"] >= 2
    assert live_event_counts["suggestion_card"] == 0
    assert payload["formal_card_creation_status"] == "not_created"
    assert payload["llm_execution_status"] == "not_called"
    assert payload["remote_asr_call_status"] == "not_called"
    assert payload["public_audio_download_status"] == "not_downloaded"
    assert payload["real_mic_shadow_readiness_status"] == (
        "blocked_not_ready_for_user_real_mic_shadow_test"
    )
    assert "asr_quality_gate_not_exited" in payload["readiness_blockers"]
    assert payload["safe_to_capture_microphone_now"] is False
    assert payload["safe_to_read_user_audio_now"] is False
    assert payload["safe_to_call_remote_asr_now"] is False
    assert payload["safe_to_call_llm_now"] is False
    assert payload["safe_to_download_models_now"] is False
    assert payload["safe_to_download_public_audio_now"] is False

    events_response = client.get("/live/asr/sessions/realistic_sim_review/events")
    drafts_response = client.get(
        "/live/asr/sessions/realistic_sim_review/llm-request-drafts"
    )

    assert events_response.status_code == 200
    assert drafts_response.status_code == 200
    events = events_response.json()["events"]
    transcript_text = "\n".join(
        str(event.get("payload", {}).get("text", "")) for event in events
    )
    assert "payment-gateway" in transcript_text
    assert "P99" in transcript_text
    assert "Kafka lag" in transcript_text
    assert "谁确认回滚 owner" in transcript_text
    assert drafts_response.json()["request_draft_count"] == live_event_counts[
        "llm_request_draft_event"
    ]


def test_desktop_realistic_meeting_simulation_pack_long_shadow_profile_creates_report_preview():
    client = TestClient(create_app())

    response = client.post(
        "/desktop/realistic-meeting-simulation-pack/sessions",
        json={"session_id": "long_shadow_sim_review", "profile": "long_shadow"},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["simulation_id"] == "realistic_meeting_simulation_pack"
    assert payload["profile_id"] == "long_shadow"
    assert payload["scenario_id"] == "pcweb_127_long_architecture_release_review"
    assert payload["session_id"] == "long_shadow_sim_review"
    assert payload["meeting_shape"] == {
        "speaker_count": 5,
        "speaker_turn_count": 16,
        "duration_seconds": 615.0,
        "overlap_marker_count": 2,
        "pause_marker_count": 5,
        "revision_count": 3,
    }
    assert payload["shadow_report_preview_status"] == "draft_preview_available_after_sse_end"
    assert "long_meeting_timeline" in payload["realism_features"]
    assert "architecture_review" in payload["realism_features"]
    assert "incident_followup" in payload["realism_features"]
    assert "idempotency-key" in payload["technical_terms"]
    assert "Redis cluster" in payload["technical_terms"]
    assert "SLO" in payload["technical_terms"]
    live_event_counts = payload["live_event_counts"]
    assert live_event_counts["transcript_partial"] >= 5
    assert live_event_counts["transcript_final"] >= 13
    assert live_event_counts["transcript_revision"] >= 3
    assert live_event_counts["state_event"] >= 12
    assert live_event_counts["llm_request_draft_event"] >= 12
    assert live_event_counts["suggestion_card"] == 0
    assert payload["llm_execution_status"] == "not_called"
    assert payload["remote_asr_call_status"] == "not_called"
    assert payload["public_audio_download_status"] == "not_downloaded"
    assert payload["safe_to_capture_microphone_now"] is False
    assert payload["safe_to_call_llm_now"] is False
    assert payload["safe_to_download_public_audio_now"] is False

    draft_response = client.get("/live/asr/sessions/long_shadow_sim_review/draft.md")
    events_response = client.get("/live/asr/sessions/long_shadow_sim_review/events")

    assert draft_response.status_code == 200
    assert events_response.status_code == 200
    draft_text = draft_response.text
    event_text = "\n".join(
        str(event.get("payload", {}).get("text", ""))
        for event in events_response.json()["events"]
    )
    assert "Draft only; not a formal gated meeting report." in draft_text
    assert "idempotency-key" in event_text
    assert "Redis cluster" in event_text
    assert "SLO" in event_text
    assert "谁确认降级开关 owner" in event_text


def test_desktop_mainline_asr_blocked_trial_creates_live_session_and_reports_dec201_quality_blocker():
    client = TestClient(create_app())

    response = client.post(
        "/desktop/mainline-asr-blocked-trial/sessions",
        json={"session_id": "mainline_asr_blocked_trial_review"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["trial_id"] == "mainline_asr_blocked_trial"
    assert body["trial_status"] == "mainline_trial_session_created"
    assert body["mainline_decision_id"] == "DEC-201"
    assert body["asr_quality_exit_status"] == "not_exited"
    assert body["asr_quality_decision_status"] == "blocked_by_funasr_smoke_assembly_input_guard"
    assert body["selected_product_route"] == "pc_product_flow_with_asr_quality_blocked_visible"
    assert body["recommended_next_action"] == "continue_pc_product_flow_keep_real_mic_blocked"
    assert body["user_can_start_real_mic_shadow_test_now"] is False
    assert body["safe_to_capture_microphone_now"] is False
    assert body["safe_to_call_remote_asr_now"] is False
    assert body["safe_to_call_llm_now"] is False
    assert [candidate["candidate_id"] for candidate in body["blocked_asr_candidates"]] == [
        "chunk10_hotword",
        "chunk20_hotword",
    ]
    assert body["blocked_asr_candidates"][0]["gate_status"] == "blocked"
    assert body["blocked_asr_candidates"][1]["quality_tradeoff"] == "speed_passes_quality_fails"
    assert body["product_replay_summary"] == {
        "funasr_engineering_preview_created_count": 3,
        "funasr_engineering_scenario_count": 4,
        "mock_engineering_preview_created_count": 4,
        "negative_control_fake_candidate_count": 0,
        "failed_funasr_scenario_id": "incident-review-001",
    }

    events_response = client.get("/live/asr/sessions/mainline_asr_blocked_trial_review/events")
    assert events_response.status_code == 200
    event_types = [event["event_type"] for event in events_response.json()["events"]]
    assert "transcript_final" in event_types
    assert "state_event" in event_types
    assert "llm_request_draft_event" in event_types
    assert "evaluation_summary" in event_types


def test_desktop_mainline_trial_feedback_export_closure_creates_preview_not_go_evidence():
    client = TestClient(create_app())
    created = client.post(
        "/desktop/mainline-asr-blocked-trial/sessions",
        json={"session_id": "mainline_trial_feedback_export_closure_review"},
    )
    assert created.status_code == 201

    response = client.post(
        "/desktop/mainline-trial-feedback-export-closures",
        json={"session_id": "mainline_trial_feedback_export_closure_review"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["pcweb_id"] == "PCWEB-129"
    assert body["closure_id"] == "mainline_trial_feedback_export_closure"
    assert body["closure_status"] == "mainline_trial_feedback_export_preview_created"
    assert body["source_trial_id"] == "mainline_asr_blocked_trial"
    assert body["candidate_report_validation_status"] == "passed"
    assert body["feedback_ingestion_status"] == "shadow_report_feedback_ingested_preview_only"
    assert body["export_readiness_status"] == "draft_export_preview_only"
    assert body["go_evidence_status"] == "not_go_evidence_replay_or_feedback_missing"
    assert body["final_decision"]["decision"] == "inconclusive_requires_more_shadow_tests"
    assert body["feedback_entry_count"] == 2
    assert body["selected_candidate_ids"] == [
        "asr_suggestion_candidate_asr_state_event_long_shadow_seg_001",
        "asr_suggestion_candidate_asr_state_event_long_shadow_seg_001_rev1",
    ]
    assert body["feedback_summary_delta"]["labels"]["useful"] == 1
    assert body["feedback_summary_delta"]["labels"]["would_have_asked"] == 1
    assert body["feedback_summary_delta"]["negative_feedback_count"] == 0
    assert body["feedback_analysis"]["useful_or_would_have_asked_count"] == 2
    assert body["feedback_analysis"]["negative_feedback_count"] == 0
    assert body["timeline_counts"]["candidate_cards"] >= 2
    assert "Draft only; not real mic validation." in body["markdown_export_preview"]
    assert "确认决策是否包含 owner" in body["markdown_export_preview"]
    assert body["safe_to_access_microphone_now"] is False
    assert body["safe_to_call_remote_asr_now"] is False
    assert body["safe_to_call_llm_now"] is False


def test_desktop_mainline_event_artifact_trial_closes_feedback_export_preview(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(app_module, "REPO_ROOT", tmp_path)
    events_path = _write_asr_events_file(
        tmp_path,
        "artifacts/tmp/asr_events/mainline-artifact-trial.events.json",
        [
            {
                "event_type": "final",
                "segment_id": "artifact_seg_001",
                "text": "我们先确认 payment-gateway 的 rollback owner。",
                "start_ms": 0,
                "end_ms": 3000,
                "received_at_ms": 3500,
                "confidence": 0.92,
            },
            {
                "event_type": "final",
                "segment_id": "artifact_seg_002",
                "text": "谁确认降级开关 owner？feature flag 半夜触发时值班群怎么通知？",
                "start_ms": 4000,
                "end_ms": 6500,
                "received_at_ms": 7000,
                "confidence": 0.91,
            },
            {
                "event_type": "final",
                "segment_id": "artifact_seg_003",
                "text": "如果 Redis cluster 缓存穿透打到 MySQL，P99 超过 900ms 就触发 rollback。",
                "start_ms": 8000,
                "end_ms": 12500,
                "received_at_ms": 13000,
                "confidence": 0.91,
            },
            {
                "event_type": "final",
                "segment_id": "artifact_seg_004",
                "text": "李四明天补充 idempotency-key 重试和 callback 失败的兼容测试。",
                "start_ms": 14000,
                "end_ms": 17500,
                "received_at_ms": 18000,
                "confidence": 0.91,
            },
            {
                "event_type": "end_of_stream",
                "segment_id": "artifact_eos",
                "text": "",
                "start_ms": 17500,
                "end_ms": 17500,
                "received_at_ms": 18100,
            },
        ],
    )
    client = TestClient(create_app())

    created = client.post(
        "/desktop/mainline-asr-event-artifact-trial/sessions",
        json={
            "session_id": "mainline_artifact_trial_closure_review",
            "provider": "local_artifact_asr",
            "events_path": events_path,
        },
    )

    assert created.status_code == 201
    created_body = created.json()
    assert created_body["trial_id"] == "mainline_asr_event_artifact_trial"
    assert created_body["trial_status"] == "mainline_artifact_trial_session_created"
    assert created_body["ingest_mode"] == "mainline_asr_event_artifact_trial"
    assert created_body["mainline_decision_id"] == "DEC-214"
    assert created_body["events_path"] == "artifacts/tmp/asr_events/mainline-artifact-trial.events.json"
    assert created_body["event_source"]["is_mock"] is False
    assert created_body["safe_to_call_remote_asr_now"] is False
    assert created_body["safe_to_call_llm_now"] is False
    assert created_body["safe_to_capture_microphone_now"] is False

    response = client.post(
        "/desktop/mainline-trial-feedback-export-closures",
        json={"session_id": "mainline_artifact_trial_closure_review"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["closure_status"] == "mainline_trial_feedback_export_preview_created"
    assert body["source_trial_id"] == "mainline_asr_event_artifact_trial"
    assert body["source_event_artifact_status"] == "local_asr_event_file_handoff_created"
    assert body["candidate_report_validation_status"] == "passed"
    assert body["feedback_ingestion_status"] == "shadow_report_feedback_ingested_preview_only"
    assert body["go_evidence_status"] == "not_go_evidence_replay_or_feedback_missing"
    assert body["final_decision"]["decision"] == "inconclusive_requires_more_shadow_tests"
    assert body["timeline_counts"]["candidate_cards"] >= 2
    assert "Draft only; not real mic validation." in body["markdown_export_preview"]
    assert body["safe_to_access_microphone_now"] is False
    assert body["safe_to_call_remote_asr_now"] is False
    assert body["safe_to_call_llm_now"] is False


def test_desktop_mainline_trial_feedback_export_closure_requires_existing_session():
    client = TestClient(create_app())

    response = client.post(
        "/desktop/mainline-trial-feedback-export-closures",
        json={"session_id": "missing_mainline_trial_feedback_export_closure"},
    )

    assert response.status_code == 404
    assert "ASR live session not found" in response.json()["detail"]


def test_desktop_mainline_trial_feedback_export_closure_rejects_non_mainline_session():
    client = TestClient(create_app())
    created = client.post(
        "/desktop/mac-local-shadow-mvp-demo/sessions",
        json={"session_id": "non_mainline_trial_feedback_export_closure"},
    )
    assert created.status_code == 201

    response = client.post(
        "/desktop/mainline-trial-feedback-export-closures",
        json={"session_id": "non_mainline_trial_feedback_export_closure"},
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["closure_status"] == "blocked_by_source_trial"
    assert detail["safe_to_access_microphone_now"] is False
    assert detail["safe_to_call_remote_asr_now"] is False
    assert detail["safe_to_call_llm_now"] is False


def test_desktop_mainline_trial_feedback_export_closure_does_not_read_secrets_probe_audio_or_write_exports(
    monkeypatch,
    tmp_path,
):
    client = TestClient(create_app())
    created = client.post(
        "/desktop/mainline-asr-blocked-trial/sessions",
        json={"session_id": "mainline_trial_feedback_export_closure_boundary"},
    )
    assert created.status_code == 201

    _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "mainline_trial_feedback_export_closure",
    )
    _install_no_native_audio_or_process_guards(
        monkeypatch,
        "mainline_trial_feedback_export_closure",
    )
    original_write_text = Path.write_text
    original_write_bytes = Path.write_bytes

    def reject_shadow_export_write_text(path, *args, **kwargs):
        if "shadow_report_exports" in Path(path).parts:
            raise AssertionError("mainline closure must not write export markdown/json files")
        return original_write_text(path, *args, **kwargs)

    def reject_shadow_export_write_bytes(path, *args, **kwargs):
        if "shadow_report_exports" in Path(path).parts:
            raise AssertionError("mainline closure must not write export bytes")
        return original_write_bytes(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", reject_shadow_export_write_text)
    monkeypatch.setattr(Path, "write_bytes", reject_shadow_export_write_bytes)

    response = client.post(
        "/desktop/mainline-trial-feedback-export-closures",
        json={"session_id": "mainline_trial_feedback_export_closure_boundary"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["closure_status"] == "mainline_trial_feedback_export_preview_created"
    assert body["export_readiness_status"] == "draft_export_preview_only"
    assert body["go_evidence_status"] == "not_go_evidence_replay_or_feedback_missing"
    assert body["safe_to_access_microphone_now"] is False
    assert body["safe_to_call_remote_asr_now"] is False
    assert body["safe_to_call_llm_now"] is False


def test_workbench_index_serves_state_first_ui_shell():
    client = TestClient(create_app())

    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Meeting Copilot" in response.text
    assert 'id="state-board"' in response.text
    assert 'id="suggestion-list"' in response.text
    assert 'id="evidence-panel"' in response.text
    assert 'id="transcript-panel"' in response.text
    assert 'id="quality-panel"' in response.text
    assert 'id="evaluation-panel"' in response.text
    assert 'id="event-stream-panel"' in response.text
    assert 'id="event-mode-replay"' in response.text
    assert 'id="event-mode-live-mock"' in response.text
    assert 'id="event-mode-live-asr"' in response.text
    assert 'id="mac-local-shadow-mvp-button"' in response.text
    assert 'id="mac-local-shadow-mvp-panel"' in response.text
    assert 'id="desktop-readiness-panel"' in response.text
    assert 'id="desktop-runtime-boundary-panel"' in response.text
    assert 'id="desktop-native-bridge-contract-panel"' in response.text
    assert 'id="desktop-native-runtime-panel"' in response.text
    assert 'id="desktop-asr-handoff-dry-run-panel"' in response.text
    assert 'id="desktop-mic-adapter-contract-panel"' in response.text
    assert 'id="desktop-real-mic-shadow-readiness-panel"' in response.text
    assert 'id="shadow-report-feedback-panel"' in response.text
    assert 'id="shadow-report-feedback-form"' in response.text
    assert 'id="shadow-report-feedback-result"' in response.text
    assert 'id="card-lifecycle-readiness-panel"' in response.text
    assert 'src="/static/app.js"' in response.text


def test_workbench_static_assets_are_served():
    client = TestClient(create_app())

    html = client.get("/")
    script = client.get("/static/app.js")
    styles = client.get("/static/styles.css")

    assert html.status_code == 200
    assert script.status_code == 200
    assert "loadFixtures" in script.text
    assert "loadDesktopShellReadiness" in script.text
    assert "renderDesktopShellReadiness" in script.text
    assert "/desktop/shell-readiness" in script.text
    assert "desktop_readiness_phases" in script.text
    assert "desktop_safe_to_capture_audio" in script.text
    assert "loadDesktopRuntimeBoundary" in script.text
    assert "renderDesktopRuntimeBoundary" in script.text
    assert "/desktop/runtime-boundary" in script.text
    assert "desktop_runtime_phases" in script.text
    assert "desktop_runtime_safe_to_create_shell" in script.text
    assert "loadDesktopNativeBridgeContract" in script.text
    assert "renderDesktopNativeBridgeContract" in script.text
    assert "/desktop/native-bridge-contract" in script.text
    assert "desktop_bridge_commands" in script.text
    assert "desktop_bridge_safe_to_create_native_bridge" in script.text
    assert "loadDesktopNativeRuntime" in script.text
    assert "renderDesktopNativeRuntime" in script.text
    assert "window.__TAURI__" in script.text
    assert "runtime_get_status" in script.text
    assert "session_prepare" in script.text
    assert "asr_worker_health" in script.text
    assert "loadDesktopAsrHandoffDryRunReadiness" in script.text
    assert "renderDesktopAsrHandoffDryRunReadiness" in script.text
    assert "/desktop/asr-worker-handoff-dry-run-readiness" in script.text
    assert "desktop_asr_handoff_phases" in script.text
    assert "desktop_asr_handoff_safe_to_start_worker" in script.text
    assert "loadDesktopMicAdapterContractReadiness" in script.text
    assert "renderDesktopMicAdapterContractReadiness" in script.text
    assert "loadDesktopRealMicShadowTestReadiness" in script.text
    assert "renderDesktopRealMicShadowTestReadiness" in script.text
    assert "/desktop/real-mic-shadow-test-readiness" in script.text
    assert "user_can_start_real_mic_shadow_test_now" in script.text
    assert "safe_to_access_microphone_from_gate_now" in script.text
    assert "desktop-real-mic-shadow-blocker" in script.text
    assert "loadDesktopMicAdapterNoopInvocation" in script.text
    assert "renderDesktopMicAdapterNoopInvocation" in script.text
    assert "loadDesktopTauriNoopRunResultCollector" in script.text
    assert "renderDesktopTauriNoopRunResultCollector" in script.text
    assert "validateDesktopTauriNoopRunResult" in script.text
    assert "/desktop/tauri-noop-run-results/validations" in script.text
    assert "desktop_tauri_noop_run_result.v1" in script.text
    assert "collector_browser_fallback" in script.text
    assert "validation_browser_fallback" in script.text
    assert "real_tauri_noop_result_ready" in script.text
    assert "pcweb_117_validation_status" in script.text
    assert "desktop-tauri-noop-result-command" in script.text
    assert "desktop-tauri-noop-validation-summary" in script.text
    assert "/desktop/mic-adapter-contract-readiness" in script.text
    assert "mic_adapter_command_catalog" in script.text
    assert "bindShadowReportFeedbackForm" in script.text
    assert "submitShadowReportFeedback" in script.text
    assert "renderShadowReportFeedbackResult" in script.text
    assert "/shadow-reports/feedback-ingestions" in script.text
    assert "shadow-report-feedback-panel" in styles.text
    assert "shadow-report-feedback-result" in styles.text
    assert 'id="mainline-feedback-export-closure-button"' in html.text
    assert 'id="mainline-closure-panel"' in html.text
    assert "loadMainlineTrialFeedbackExportClosure" in script.text
    assert "/desktop/mainline-trial-feedback-export-closures" in script.text
    assert "renderMainlineTrialFeedbackExportClosure" in script.text
    assert "mainline-closure-panel" in styles.text
    for mic_adapter_noop_command in [
        "runtime_get_status",
        "session_prepare",
        "asr_worker_health",
        "mic_adapter_prepare",
        "mic_adapter_status",
        "mic_adapter_start",
        "mic_adapter_pause",
        "mic_adapter_resume",
        "mic_adapter_stop",
        "mic_adapter_delete_audio_chunks",
    ]:
        assert mic_adapter_noop_command in script.text
    assert "mic_adapter_browser_fallback" in script.text
    assert "desktop-mic-adapter-invoke-command" in script.text
    for mic_adapter_flag in [
        "safe_to_bind_mic_adapter_now",
        "safe_to_accept_mic_command_now",
        "safe_to_execute_mic_command_now",
        "safe_to_select_input_device_now",
        "safe_to_request_audio_permission_now",
        "safe_to_capture_audio_now",
        "safe_to_start_recording_now",
        "safe_to_pause_recording_now",
        "safe_to_resume_recording_now",
        "safe_to_stop_recording_now",
        "safe_to_write_audio_chunk_now",
        "safe_to_read_audio_chunk_now",
        "safe_to_delete_audio_chunks_now",
        "safe_to_read_user_audio_now",
        "safe_to_read_configs_local_now",
        "safe_to_read_secret_now",
        "safe_to_call_remote_asr_now",
        "safe_to_call_llm_now",
        "safe_to_download_models_now",
        "safe_to_mutate_web_session_now",
        "safe_to_run_tauri_or_cargo_now",
    ]:
        assert mic_adapter_flag in script.text
    assert "desktop-native-runtime-panel" in styles.text
    assert "desktop-asr-handoff-dry-run-panel" in styles.text
    assert "desktop-mic-adapter-contract-panel" in styles.text
    assert "desktop-real-mic-shadow-readiness-panel" in styles.text
    assert "desktop-real-mic-shadow-blocker" in styles.text
    assert "mac-local-shadow-mvp-panel" in styles.text
    assert "mac-local-shadow-mvp-metric" in styles.text
    assert "desktop-mic-adapter-invoke-list" in styles.text
    assert "desktop-mic-adapter-invoke-command" in styles.text
    assert "desktop-tauri-noop-result-list" in styles.text
    assert "desktop-tauri-noop-result-command" in styles.text
    assert "desktop-tauri-noop-validation-summary" in styles.text
    assert "desktop-runtime-boundary-panel" in styles.text
    assert "desktop-native-bridge-contract-panel" in styles.text
    load_fixtures_body = script.text.split("async function loadFixtures()", 1)[1].split(
        "async function loadSelectedFixture", 1
    )[0]
    assert "loadSelectedFixture" not in load_fixtures_body
    render_empty_body = script.text.split("function renderEmpty()", 1)[1].split(
        "async function setEventMode", 1
    )[0]
    assert "loadDesktopShellReadiness();" in render_empty_body
    assert "loadDesktopRuntimeBoundary();" in render_empty_body
    assert "loadDesktopNativeBridgeContract();" in render_empty_body
    assert "loadDesktopNativeRuntime();" in render_empty_body
    assert "loadDesktopAsrHandoffDryRunReadiness();" in render_empty_body
    assert "loadDesktopMicAdapterContractReadiness();" in render_empty_body
    assert "loadDesktopRealMicShadowTestReadiness();" in render_empty_body
    assert "renderSuggestionCards" in script.text
    assert "renderEvaluationSummary" in script.text
    assert "loadEventStream" in script.text
    assert "currentEventMode" in script.text
    assert "loadLiveMockSession" in script.text
    assert "loadLiveAsrSession" in script.text
    assert "loadMacLocalShadowMvpDemo" in script.text
    assert "loadRealisticMeetingSimulationPack" in script.text
    assert "loadLongRealisticMeetingSimulationPack" in script.text
    assert "mainline-asr-blocked-trial-button" in html.text
    assert "mainline-asr-event-artifact-trial-button" in html.text
    assert 'class="app-shell"' in html.text
    assert 'class="brand-lockup"' in html.text
    assert 'class="toolbar-group primary-flow"' in html.text
    assert 'class="toolbar-group support-flow"' in html.text
    assert 'class="mainline-status-strip"' in html.text
    assert "--color-background: #0f172a" in styles.text
    assert ".app-shell" in styles.text
    assert ".brand-mark" in styles.text
    assert ".mainline-status-strip" in styles.text
    assert "@media (prefers-reduced-motion: reduce)" in styles.text
    assert "loadMainlineAsrBlockedTrial" in script.text
    assert "loadMainlineAsrEventArtifactTrial" in script.text
    assert "/desktop/mac-local-shadow-mvp-demo/sessions" in script.text
    assert "/desktop/realistic-meeting-simulation-pack/sessions" in script.text
    assert "/desktop/mainline-asr-blocked-trial/sessions" in script.text
    assert "/desktop/mainline-asr-event-artifact-trial/sessions" in script.text
    assert "mainline_asr_event_artifact_trial" in script.text
    assert "isMainlineTrialSession" in script.text
    assert "source_event_artifact_status" in script.text
    assert "profile" in script.text
    assert "long_shadow" in script.text
    assert "mac_local_shadow_mvp" in script.text
    assert "realistic_meeting_simulation_pack" in script.text
    assert "mainline_asr_blocked_trial" in script.text
    assert "renderMacLocalShadowMvp" in script.text
    assert "renderRealisticMeetingSimulationPack" in script.text
    assert "renderMainlineAsrBlockedTrial" in script.text
    assert "closed_to_no_llm_request_draft_and_readiness_blockers" in script.text
    assert "continue_pc_product_flow_keep_real_mic_blocked" in script.text
    assert "loadLiveAsrDraft" in script.text
    assert "return loadLiveAsrDraft();" in script.text
    assert "connectLiveEventStream" in script.text
    assert "closeLiveEventStream" in script.text
    assert "createLiveIncrementalSnapshot" in script.text
    assert "applyLiveEventToSnapshot" in script.text
    assert "snapshotLookup" in script.text
    assert "stateTypeToCollection" in script.text
    assert "upsertById" in script.text
    assert "applyLiveCardStatus" in script.text
    assert 'if (currentEventMode === "live_mock")' in script.text
    assert 'if (currentEventMode === "live_asr")' in script.text
    assert 'document.getElementById("report-panel").textContent = ""' in script.text
    assert "payload.evidence_spans" in script.text
    assert "payload.superseded_evidence_spans" in script.text
    assert "staleEvidenceIdsForCard" in script.text
    assert "missingEvidenceIdsForCard" in script.text
    assert "suggestion_invalidated" in script.text
    assert "suggestion_candidate_event" in script.text
    assert "llm_request_draft_event" in script.text
    assert "payload.candidate_id" in script.text
    assert "payload.request_id" in script.text
    assert "payload.target_candidate_id" in script.text
    assert "payload.request_status" in script.text
    assert "payload.schema_status" in script.text
    assert "payload.input_summary" in script.text
    assert "payload.source_event_ids" in script.text
    assert "payload.evidence_span_ids" in script.text
    assert "payload.segment_batch" in script.text
    assert "payload.confidence_level" in script.text
    assert "payload.confidence" in script.text
    assert "payload.degradation_reasons" in script.text
    assert "loadLiveAsrCardLifecycleReadinessSummary" in script.text
    assert "buildCardLifecycleReadinessCandidateResponse" in script.text
    assert "renderCardLifecycleReadinessSummary" in script.text
    assert "llm-card-lifecycle-readiness-summaries" in script.text
    assert "card_lifecycle_summary_phases" in script.text
    assert "card_lifecycle_safe_to_execute_llm" in script.text
    assert "const readinessEvents = events.length > 1 ? events : liveStreamEvents;" in script.text
    assert "loadLiveAsrCardLifecycleReadinessSummary(readinessEvents);" in script.text
    assert 'return "action_gap";' not in script.text
    assert 'return "risk_gap";' not in script.text
    assert 'return "followup_gap";' not in script.text
    assert "evidence: stale" in script.text
    assert "evidence: missing" in script.text
    assert "payload.state_item" in script.text
    assert "payload.card" in script.text
    assert "evidence.revision_of" in script.text
    assert "evidence.replaced_by" in script.text
    assert "new EventSource(" in script.text
    assert "events.sse" in script.text
    assert "addEventListener(eventType" in script.text
    assert "liveEventSource.close()" in script.text
    assert "window.__meetingCopilotLiveStreamClosed = true" in script.text
    assert "event.event_type === \"evaluation_summary\"" in script.text
    assert "/live/mock/fixtures/" in script.text
    assert "/live/asr/mock/sessions" in script.text
    assert "/live/asr/sessions/" in script.text
    assert "/draft.md" in script.text
    assert "/live/sessions/" in script.text
    assert "event-mode-live-mock" in script.text
    assert "event-mode-live-asr" in script.text
    assert "renderEventStream" in script.text
    assert "syncEvaluationFromEvents" in script.text
    assert "currentEvaluationSummary = event.payload || null" in script.text
    assert "payload.decision_reason" in script.text
    assert "payload.llm_call_status" in script.text
    assert "payload.cooldown_remaining_ms" in script.text
    assert "await loadEventStream();" in script.text
    assert "isActionableCard" in script.text
    assert "suggestion_silenced" in script.text
    assert "schemaResultClass" in script.text
    assert "Schema Blocked" in script.text
    assert "Silenced" in script.text
    assert "segmentByEvidenceId" in script.text
    assert 'chip.dataset.evidenceId = evidenceId' in script.text
    assert 'const segmentId = segmentByEvidence[evidenceId] || ""' in script.text
    assert "chip.dataset.segmentId = segmentId" in script.text
    assert "evidenceLifecycleClass" in script.text
    assert "evidence.status" in script.text
    assert "focusTranscriptSegment" in script.text
    assert 'target.classList.add("active")' in script.text
    assert "transcript-segment-" in script.text
    assert styles.status_code == 200
    assert "state-board" in styles.text
    assert "suggestion-card" in styles.text
    assert "evaluation-panel" in styles.text
    assert "event-stream-panel" in styles.text
    assert "suggestion-card.muted" in styles.text
    assert "event-item.suggestion_silenced" in styles.text
    assert "event-mode-control" in styles.text
    assert "event-item.transcript_partial" in styles.text
    assert "event-item.scheduler_event" in styles.text
    assert "event-item.live_asr_stream" in styles.text
    assert ".segment-item.active" in styles.text
    assert ".evidence-item.stale" in styles.text
    assert ".chip.stale" in styles.text


def test_workbench_static_assets_include_local_shadow_preview_release_summary():
    client = TestClient(create_app())

    html = client.get("/")
    script = client.get("/static/app.js")
    styles = client.get("/static/styles.css")

    assert html.status_code == 200
    assert script.status_code == 200
    assert styles.status_code == 200
    assert "local-shadow-preview-release-panel" in html.text
    assert "Local Synthetic Preview" in html.text
    assert "真实麦克风准备度（Blocked）" in html.text
    assert "Shadow MVP" not in html.text
    assert "Mac Local Shadow MVP" not in html.text
    assert "真实会议验收" not in html.text
    assert "loadLocalShadowPreviewReleaseReadiness" in script.text
    assert "/desktop/local-shadow-preview-release-readiness" in script.text
    assert "formal_report_status" in script.text
    assert "safety_flags" in script.text
    assert "release-summary-flags" in script.text
    assert "local-shadow-preview-release" in styles.text


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


def test_asr_live_llm_openai_request_body_previews_endpoint_returns_preview_queue_without_calling_llm(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "llm-gateway.local.json"
    config_path.write_text(
        (
            '{"base_url":"https://openai-body-preview-read-sentinel.invalid",'
            '"api_key":"TEST_OPENAI_BODY_PREVIEW_CONFIG_SECRET",'
            '"model":"openai-body-preview-config-model",'
            '"authorization":"Bearer OPENAI_BODY_PREVIEW_CONFIG_BEARER"}'
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MEETING_COPILOT_LLM_CONFIG", str(config_path))
    monkeypatch.setenv("OPENAI_API_KEY", "TEST_OPENAI_BODY_PREVIEW_ENV_OPENAI_KEY")
    monkeypatch.setenv(
        "MEETING_COPILOT_LLM_API_KEY",
        "TEST_OPENAI_BODY_PREVIEW_ENV_MEETING_KEY",
    )
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

    def is_llm_config_path(path) -> bool:
        return Path(path) == config_path

    def reject_llm_config_read_text(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError(
                "llm openai request body preview must not read config files"
            )
        return original_read_text(path, *args, **kwargs)

    def reject_llm_config_read_bytes(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError(
                "llm openai request body preview must not read config bytes"
            )
        return original_read_bytes(path, *args, **kwargs)

    def reject_llm_config_path_open(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError(
                "llm openai request body preview must not open config files"
            )
        return original_path_open(path, *args, **kwargs)

    def reject_llm_config_builtin_open(file, *args, **kwargs):
        if is_llm_config_path(file):
            raise AssertionError(
                "llm openai request body preview must not open config files"
            )
        return original_builtin_open(file, *args, **kwargs)

    def reject_llm_config_exists(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError(
                "llm openai request body preview must not check config existence"
            )
        return original_path_exists(path, *args, **kwargs)

    def reject_llm_config_is_file(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError(
                "llm openai request body preview must not check config file type"
            )
        return original_path_is_file(path, *args, **kwargs)

    def reject_llm_config_path_stat(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError(
                "llm openai request body preview must not stat config files"
            )
        return original_path_stat(path, *args, **kwargs)

    def reject_llm_config_os_stat(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError(
                "llm openai request body preview must not stat config files"
            )
        return original_os_stat(path, *args, **kwargs)

    def reject_llm_secret_getenv(key, *args, **kwargs):
        if key in {"OPENAI_API_KEY", "MEETING_COPILOT_LLM_API_KEY"}:
            raise AssertionError(
                "llm openai request body preview must not read env secrets"
            )
        return original_getenv(key, *args, **kwargs)

    def reject_llm_secret_environ_get(key, *args, **kwargs):
        if key in {"OPENAI_API_KEY", "MEETING_COPILOT_LLM_API_KEY"}:
            raise AssertionError(
                "llm openai request body preview must not read env secrets"
            )
        return original_environ_get(key, *args, **kwargs)

    def reject_llm_gateway_config_load(*args, **kwargs):
        raise AssertionError(
            "llm openai request body preview must not load llm gateway config"
        )

    def reject_keychain_access(*args, **kwargs):
        raise AssertionError("llm openai request body preview must not access keychain")

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
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="local_asr_openai_request_body_preview_review"),
    )
    events_before_response = client.get(
        "/live/asr/sessions/local_asr_openai_request_body_preview_review/events"
    )

    response = client.get(
        "/live/asr/sessions/local_asr_openai_request_body_preview_review/llm-openai-request-body-previews"
    )
    events_after_response = client.get(
        "/live/asr/sessions/local_asr_openai_request_body_preview_review/events"
    )

    assert create_response.status_code == 201
    assert events_before_response.status_code == 200
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == "local_asr_openai_request_body_preview_review"
    assert body["source"] == "live_asr_stream"
    assert body["trace_kind"] == "live_event"
    assert body["provider_protocol"] == "openai_compatible_chat_completions"
    assert body["preview_status"] == "body_preview_only"
    assert body["redaction_policy"] == "local_sensitive_draft_value_guard.v1"
    assert body["redaction_status"] == "not_needed"
    assert body["redacted_preview_count"] == 0
    assert body["llm_call_status"] == "not_called"
    assert body["credentials_status"] == "not_read"
    assert body["config_source_status"] == "not_read"
    assert body["schema_status"] == "not_generated"
    assert body["card_status"] == "not_created"
    assert body["cost_status"] == "not_estimated"
    assert body["safe_to_execute"] is False
    assert body["request_body_preview_count"] == 5
    previews = body["request_body_previews"]
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
        "request_body_preview_id": (
            "asr_openai_request_body_preview_"
            "asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001"
        ),
        "request_body_status": "preview_only",
        "redaction_status": "not_needed",
        "redacted_fields": [],
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
        "idempotency_key": (
            "live_asr_openai_request_body_preview:"
            "local_asr_openai_request_body_preview_review:"
            "asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001"
        ),
        "provider_protocol": "openai_compatible_chat_completions",
        "endpoint_family": "chat_completions",
        "http_method": "POST",
        "request_path": "/v1/chat/completions",
        "model": "not_configured",
        "temperature": 0.2,
        "max_output_tokens": 600,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are Meeting Copilot. Generate one concise suggestion card "
                    "for an engineering meeting. Use only the provided evidence."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Target: DecisionCandidate asr_decision_asr_seg_001\n"
                    "Gap rule: release.rollback.owner.required\n"
                    "Evidence spans: asr_ev_asr_seg_001\n"
                    "Segment batch: asr_seg_001\n"
                    "Candidate quality: high (0.9)\n"
                    "Suggested prompt: 确认决策是否包含 owner、回滚条件和监控口径。\n"
                    "Input summary: DecisionCandidate asr_decision_asr_seg_001 "
                    "from asr_seg_001 using asr_ev_asr_seg_001"
                ),
            },
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": _expected_suggestion_card_schema_outline_preview(),
        },
        "metadata": {
            "source": "live_asr_stream",
            "trace_kind": "live_event",
            "request_origin": "local_deterministic_asr_request_draft",
            "source_event_ids": ["asr_state_event_asr_seg_001"],
            "evidence_span_ids": ["asr_ev_asr_seg_001"],
            "segment_batch": ["asr_seg_001"],
            "candidate_confidence": 0.9,
            "candidate_confidence_level": "high",
            "candidate_degradation_reasons": [],
        },
        "forbidden_request_fields": [
            "api_key",
            "authorization",
            "bearer_token",
            "base_url",
            "raw_config",
            "config_path",
        ],
        "llm_call_status": "not_called",
        "schema_status": "not_generated",
        "card_status": "not_created",
        "cost_status": "not_estimated",
    }
    assert {preview["request_body_status"] for preview in previews} == {
        "preview_only"
    }
    assert {preview["llm_call_status"] for preview in previews} == {"not_called"}
    assert {preview["schema_status"] for preview in previews} == {"not_generated"}
    assert {preview["card_status"] for preview in previews} == {"not_created"}
    assert {preview["cost_status"] for preview in previews} == {"not_estimated"}
    assert {preview["model"] for preview in previews} == {"not_configured"}
    assert all(preview["messages"][0]["role"] == "system" for preview in previews)
    assert all(preview["messages"][1]["role"] == "user" for preview in previews)
    assert body["forbidden_request_fields"] == [
        "api_key",
        "authorization",
        "bearer_token",
        "base_url",
        "raw_config",
        "config_path",
    ]
    assert body["block_reasons"] == [
        "request_body_preview_only",
        "provider_config_not_loaded",
        "credentials_not_read",
        "llm_executor_disabled",
    ]
    for forbidden in (
        str(config_path),
        "openai-body-preview-read-sentinel.invalid",
        "TEST_OPENAI_BODY_PREVIEW_CONFIG_SECRET",
        "openai-body-preview-config-model",
        "OPENAI_BODY_PREVIEW_CONFIG_BEARER",
        "TEST_OPENAI_BODY_PREVIEW_ENV_OPENAI_KEY",
        "TEST_OPENAI_BODY_PREVIEW_ENV_MEETING_KEY",
        "Bearer",
        "sk-",
    ):
        assert forbidden not in response.text
    for forbidden_key in (
        "api_key_present",
        "api_key_valid",
        "api_key_length",
        "api_key_hash",
        "api_key_prefix",
        "api_key_suffix",
        "api_key_fingerprint",
    ):
        assert forbidden_key not in response.text
    assert events_after_response.status_code == 200
    assert events_before_response.json()["events"] == events_after_response.json()["events"]
    assert events_after_response.json()["events"] == create_response.json()["live_events"]


def test_asr_live_llm_openai_request_body_previews_endpoint_redacts_sensitive_draft_payload_without_mutating_record(
    tmp_path,
):
    relay_domain = "codexai" + ".club"
    relay_url = "https://" + relay_domain + "/v1"
    secret_like_values = [
        "sk-" + "TEST_OPENAI_BODY_PREVIEW_DRAFT_SECRET",
        "Authorization: Bearer TEST_OPENAI_BODY_PREVIEW_AUTH_HEADER",
        "Bearer TEST_OPENAI_BODY_PREVIEW_SOURCE_EVENT_BEARER",
        "sk-" + "TEST_OPENAI_BODY_PREVIEW_EVIDENCE_SECRET",
        "Bearer TEST_OPENAI_BODY_PREVIEW_ORIGIN_BEARER",
        "configs/local/llm-gateway.local.json",
        "configs/local/segment-secret.json",
        relay_url,
    ]
    record = {
        "session_id": "persisted_asr_openai_request_body_preview_redaction_review",
        "provider": "local_mock_asr",
        "source": "live_asr_stream",
        "trace_kind": "live_event",
        "events": [
            {
                "id": "llm_request_draft:sensitive_state_event",
                "event_type": "llm_request_draft_event",
                "at_ms": 1200,
                "sequence": 1,
                "source": "live_asr_stream",
                "trace_kind": "live_event",
                "payload": {
                    "request_id": "asr_llm_request_draft_sensitive",
                    "request_type": "llm_suggestion_card_draft",
                    "request_status": "draft_only",
                    "target_candidate_id": "asr_suggestion_candidate_sensitive",
                    "target_type": "DecisionCandidate",
                    "target_id": "asr_decision_sensitive",
                    "gap_rule_id": "release.rollback.owner.required",
                    "prompt_version": "not-called",
                    "model": "not-called",
                    "llm_call_status": "not_called",
                    "card_status": "not_created",
                    "schema_status": "not_generated",
                    "suggested_prompt": (
                        "请检查 Authorization: Bearer TEST_OPENAI_BODY_PREVIEW_AUTH_HEADER "
                        "和 api_key="
                        + "sk-"
                        + "TEST_OPENAI_BODY_PREVIEW_DRAFT_SECRET"
                    ),
                    "input_summary": (
                        "raw_config includes base_url "
                        + relay_url
                        + " and "
                        "config_path configs/local/llm-gateway.local.json"
                    ),
                    "source_event_ids": [
                        "asr_state_event_sensitive",
                        "Bearer TEST_OPENAI_BODY_PREVIEW_SOURCE_EVENT_BEARER",
                    ],
                    "evidence_span_ids": [
                        "asr_ev_sensitive",
                        "sk-" + "TEST_OPENAI_BODY_PREVIEW_EVIDENCE_SECRET",
                    ],
                    "segment_batch": [
                        "asr_seg_sensitive",
                        "configs/local/segment-secret.json",
                    ],
                    "candidate_confidence": 0.9,
                    "candidate_confidence_level": "high",
                    "candidate_degradation_reasons": [],
                    "request_origin": (
                        "local_deterministic_asr_request_draft "
                        "Bearer TEST_OPENAI_BODY_PREVIEW_ORIGIN_BEARER"
                    ),
                    "source": "live_asr_stream",
                },
            },
            {
                "id": "evaluation:asr_stream_summary",
                "event_type": "evaluation_summary",
                "at_ms": 1300,
                "sequence": 2,
                "source": "live_asr_stream",
                "trace_kind": "live_event",
                "payload": {
                    "source": "live_asr_stream",
                    "provider": "local_mock_asr",
                    "is_mock": True,
                    "passes_minimum_gate": True,
                    "partial_event_count": 0,
                    "final_event_count": 1,
                    "revision_event_count": 0,
                    "error_event_count": 0,
                    "end_of_stream_event_count": 1,
                },
            },
        ],
    }
    client = TestClient(create_app(data_dir=tmp_path))
    repository = app_module.JsonFileAsrLiveSessionRepository(tmp_path)
    repository.create(record)

    events_before_response = client.get(
        "/live/asr/sessions/persisted_asr_openai_request_body_preview_redaction_review/events"
    )
    drafts_before_response = client.get(
        "/live/asr/sessions/persisted_asr_openai_request_body_preview_redaction_review/llm-request-drafts"
    )
    response = client.get(
        "/live/asr/sessions/persisted_asr_openai_request_body_preview_redaction_review/llm-openai-request-body-previews"
    )
    events_after_response = client.get(
        "/live/asr/sessions/persisted_asr_openai_request_body_preview_redaction_review/events"
    )
    drafts_after_response = client.get(
        "/live/asr/sessions/persisted_asr_openai_request_body_preview_redaction_review/llm-request-drafts"
    )

    assert events_before_response.status_code == 200
    assert drafts_before_response.status_code == 200
    assert response.status_code == 200
    body = response.json()
    assert body["redaction_policy"] == "local_sensitive_draft_value_guard.v1"
    assert body["redaction_status"] == "applied"
    assert body["redacted_preview_count"] == 1
    assert body["request_body_preview_count"] == 1
    preview = body["request_body_previews"][0]
    assert preview["redaction_status"] == "applied"
    assert preview["redacted_fields"] == [
        "suggested_prompt",
        "input_summary",
        "request_origin",
        "source_event_ids",
        "evidence_span_ids",
        "segment_batch",
    ]
    assert "[redacted:sensitive_draft_value]" in preview["messages"][1]["content"]
    assert preview["metadata"]["request_origin"] == "[redacted:sensitive_draft_value]"
    assert preview["metadata"]["source_event_ids"] == [
        "asr_state_event_sensitive",
        "[redacted:sensitive_draft_value]",
    ]
    assert preview["metadata"]["evidence_span_ids"] == [
        "asr_ev_sensitive",
        "[redacted:sensitive_draft_value]",
    ]
    assert preview["metadata"]["segment_batch"] == [
        "asr_seg_sensitive",
        "[redacted:sensitive_draft_value]",
    ]
    for sensitive_value in secret_like_values:
        assert sensitive_value not in response.text
    preview_reflected_text = json.dumps(
        {
            "messages": preview["messages"],
            "metadata": preview["metadata"],
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    for sensitive_marker in (
        "raw_config",
        "api_key",
        "base_url",
        "config_path",
        "configs/local",
        relay_domain,
    ):
        assert sensitive_marker not in preview_reflected_text
    assert events_after_response.status_code == 200
    assert drafts_after_response.status_code == 200
    assert events_before_response.json()["events"] == record["events"]
    assert events_after_response.json()["events"] == record["events"]
    assert drafts_before_response.json()["request_drafts"][0]["payload"] == record["events"][0]["payload"]
    assert drafts_after_response.json()["request_drafts"][0]["payload"] == record["events"][0]["payload"]
    for sensitive_value in secret_like_values:
        assert sensitive_value in drafts_after_response.text


def test_asr_live_llm_openai_request_body_previews_endpoint_includes_suggestion_card_schema_outline_without_mutating_events():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(
            session_id="local_asr_openai_request_body_schema_outline_review"
        ),
    )
    events_before_response = client.get(
        "/live/asr/sessions/local_asr_openai_request_body_schema_outline_review/events"
    )

    response = client.get(
        "/live/asr/sessions/local_asr_openai_request_body_schema_outline_review/llm-openai-request-body-previews"
    )
    events_after_response = client.get(
        "/live/asr/sessions/local_asr_openai_request_body_schema_outline_review/events"
    )

    assert create_response.status_code == 201
    assert events_before_response.status_code == 200
    assert response.status_code == 200
    body = response.json()
    assert body["schema_status"] == "not_generated"
    assert body["request_body_preview_count"] == 5
    response_format = body["request_body_previews"][0]["response_format"]
    assert response_format["type"] == "json_schema"
    json_schema = response_format["json_schema"]
    assert json_schema["name"] == "SuggestionCardV1"
    assert json_schema["strict"] is True
    assert json_schema["schema_outline_status"] == "outline_only"
    assert json_schema["schema_outline_source"] == "local_contract_preview"
    schema_outline = json_schema["schema_outline"]
    assert schema_outline["type"] == "object"
    expected_outline = _expected_suggestion_card_schema_outline_preview()[
        "schema_outline"
    ]
    assert schema_outline["required"] == expected_outline["required"]
    assert schema_outline["optional"] == expected_outline["optional"]
    assert schema_outline["additional_properties_status"] == (
        expected_outline["additional_properties_status"]
    )
    assert schema_outline["properties"] == expected_outline["properties"]
    assert events_after_response.status_code == 200
    assert events_before_response.json()["events"] == events_after_response.json()["events"]
    assert events_after_response.json()["events"] == create_response.json()["live_events"]


def test_asr_live_llm_openai_request_body_previews_endpoint_returns_empty_queue_for_transcript_only_session():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json={
            "session_id": "local_asr_openai_request_body_preview_empty_review",
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
        "/live/asr/sessions/local_asr_openai_request_body_preview_empty_review/llm-openai-request-body-previews"
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    assert response.json() == {
        "session_id": "local_asr_openai_request_body_preview_empty_review",
        "source": "live_asr_stream",
        "trace_kind": "live_event",
        "provider_protocol": "openai_compatible_chat_completions",
        "preview_status": "body_preview_only",
        "redaction_policy": "local_sensitive_draft_value_guard.v1",
        "redaction_status": "not_needed",
        "redacted_preview_count": 0,
        "llm_call_status": "not_called",
        "credentials_status": "not_read",
        "config_source_status": "not_read",
        "schema_status": "not_generated",
        "card_status": "not_created",
        "cost_status": "not_estimated",
        "safe_to_execute": False,
        "request_body_preview_count": 0,
        "request_body_previews": [],
        "forbidden_request_fields": [
            "api_key",
            "authorization",
            "bearer_token",
            "base_url",
            "raw_config",
            "config_path",
        ],
        "block_reasons": [
            "request_body_preview_only",
            "provider_config_not_loaded",
            "credentials_not_read",
            "llm_executor_disabled",
            "no_request_drafts",
        ],
        "next_required_decisions": [
            "authorized_config_file_reader",
            "secret_storage_adapter",
            "enabled_executor_mode_contract",
            "schema_validation_and_card_lifecycle",
            "token_cost_accounting",
        ],
    }


def test_asr_live_llm_openai_request_body_previews_endpoint_reads_persisted_record_across_app_instances(
    tmp_path,
):
    first_client = TestClient(create_app(data_dir=tmp_path))
    create_response = first_client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(
            session_id="persisted_asr_openai_request_body_preview_review"
        ),
    )

    second_client = TestClient(create_app(data_dir=tmp_path))
    response = second_client.get(
        "/live/asr/sessions/persisted_asr_openai_request_body_preview_review/llm-openai-request-body-previews"
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["request_body_preview_count"] == 5
    assert [preview["request_id"] for preview in body["request_body_previews"]] == [
        event["payload"]["request_id"]
        for event in create_response.json()["live_events"]
        if event["event_type"] == "llm_request_draft_event"
    ]
    assert body["request_body_previews"][0]["idempotency_key"] == (
        "live_asr_openai_request_body_preview:"
        "persisted_asr_openai_request_body_preview_review:"
        "asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001"
    )


def test_asr_live_llm_openai_request_body_previews_endpoint_returns_404_for_missing_session():
    client = TestClient(create_app())

    response = client.get(
        "/live/asr/sessions/missing_asr_review/llm-openai-request-body-previews"
    )

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


def test_asr_live_llm_execution_runs_disabled_endpoint_rejects_unsupported_mode():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="local_asr_execution_mode_review"),
    )

    response = client.post(
        "/live/asr/sessions/local_asr_execution_mode_review/llm-execution-runs",
        json={"mode": "enabled"},
    )

    assert create_response.status_code == 201
    assert response.status_code == 422
    assert "unsupported llm execution mode: enabled" in response.text


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


def test_asr_live_llm_schema_validation_dry_run_endpoint_passes_candidate_response_without_calling_llm(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "llm-gateway.local.json"
    config_path.write_text(
        (
            '{"base_url":"https://schema-validation-read-sentinel.invalid",'
            '"api_key":"TEST_SCHEMA_VALIDATION_CONFIG_SECRET",'
            '"model":"schema-validation-config-model",'
            '"authorization":"Bearer SCHEMA_VALIDATION_CONFIG_BEARER"}'
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MEETING_COPILOT_LLM_CONFIG", str(config_path))
    monkeypatch.setenv("OPENAI_API_KEY", "TEST_SCHEMA_VALIDATION_ENV_OPENAI_KEY")
    monkeypatch.setenv(
        "MEETING_COPILOT_LLM_API_KEY",
        "TEST_SCHEMA_VALIDATION_ENV_MEETING_KEY",
    )
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

    def is_llm_config_path(path) -> bool:
        return Path(path) == config_path

    def reject_llm_config_read_text(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError(
                "llm schema validation dry-run must not read config files"
            )
        return original_read_text(path, *args, **kwargs)

    def reject_llm_config_read_bytes(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError(
                "llm schema validation dry-run must not read config bytes"
            )
        return original_read_bytes(path, *args, **kwargs)

    def reject_llm_config_path_open(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError(
                "llm schema validation dry-run must not open config files"
            )
        return original_path_open(path, *args, **kwargs)

    def reject_llm_config_builtin_open(file, *args, **kwargs):
        if is_llm_config_path(file):
            raise AssertionError(
                "llm schema validation dry-run must not open config files"
            )
        return original_builtin_open(file, *args, **kwargs)

    def reject_llm_config_exists(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError(
                "llm schema validation dry-run must not check config existence"
            )
        return original_path_exists(path, *args, **kwargs)

    def reject_llm_config_is_file(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError(
                "llm schema validation dry-run must not check config file type"
            )
        return original_path_is_file(path, *args, **kwargs)

    def reject_llm_config_path_stat(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError(
                "llm schema validation dry-run must not stat config files"
            )
        return original_path_stat(path, *args, **kwargs)

    def reject_llm_config_os_stat(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError(
                "llm schema validation dry-run must not stat config files"
            )
        return original_os_stat(path, *args, **kwargs)

    def reject_llm_secret_getenv(key, *args, **kwargs):
        if key in {"OPENAI_API_KEY", "MEETING_COPILOT_LLM_API_KEY"}:
            raise AssertionError(
                "llm schema validation dry-run must not read env secrets"
            )
        return original_getenv(key, *args, **kwargs)

    def reject_llm_secret_environ_get(key, *args, **kwargs):
        if key in {"OPENAI_API_KEY", "MEETING_COPILOT_LLM_API_KEY"}:
            raise AssertionError(
                "llm schema validation dry-run must not read env secrets"
            )
        return original_environ_get(key, *args, **kwargs)

    def reject_llm_gateway_config_load(*args, **kwargs):
        raise AssertionError(
            "llm schema validation dry-run must not load llm gateway config"
        )

    def reject_keychain_access(*args, **kwargs):
        raise AssertionError("llm schema validation dry-run must not access keychain")

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
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(
            session_id="local_asr_schema_validation_dry_run_review"
        ),
    )
    events_before_response = client.get(
        "/live/asr/sessions/local_asr_schema_validation_dry_run_review/events"
    )

    response = client.post(
        "/live/asr/sessions/local_asr_schema_validation_dry_run_review/llm-schema-validation-dry-runs",
        json={
            "mode": "dry_run_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )
    events_after_response = client.get(
        "/live/asr/sessions/local_asr_schema_validation_dry_run_review/events"
    )

    assert create_response.status_code == 201
    assert events_before_response.status_code == 200
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "session_id": "local_asr_schema_validation_dry_run_review",
        "source": "live_asr_stream",
        "trace_kind": "live_event",
        "validation_mode": "dry_run_only",
        "validation_status": "passed",
        "schema_name": "SuggestionCardV1",
        "schema_validation_status": "dry_run_passed",
        "schema_result_status": "not_generated",
        "card_status": "not_created",
        "llm_call_status": "not_called",
        "credentials_status": "not_read",
        "config_source_status": "not_read",
        "cost_status": "not_estimated",
        "safe_to_create_card": False,
        "request_id": (
            "asr_llm_request_draft_"
            "asr_suggestion_candidate_asr_state_event_asr_seg_001"
        ),
        "request_draft_event_id": "llm_request_draft:asr_state_event_asr_seg_001",
        "request_draft_sequence": 6,
        "target_candidate_id": (
            "asr_suggestion_candidate_asr_state_event_asr_seg_001"
        ),
        "target_type": "DecisionCandidate",
        "target_id": "asr_decision_asr_seg_001",
        "gap_rule_id": "release.rollback.owner.required",
        "source_event_ids": ["asr_state_event_asr_seg_001"],
        "evidence_span_ids": ["asr_ev_asr_seg_001"],
        "segment_batch": ["asr_seg_001"],
        "validation_errors": [],
        "validated_field_count": 21,
        "candidate_response_preview": {
            "id": "card_dry_run_001",
            "type": "owner_gap",
            "schema_result": "valid",
            "show_or_silence_decision": "show",
            "status": "new",
        },
        "block_reasons": [
            "schema_validation_dry_run_only",
            "llm_executor_disabled",
            "card_lifecycle_disabled",
        ],
        "next_required_decisions": [
            "enabled_executor_mode_contract",
            "real_llm_response_parser",
            "schema_validation_failure_lifecycle",
            "card_creation_policy",
            "token_cost_accounting",
        ],
    }
    assert events_after_response.status_code == 200
    assert events_before_response.json()["events"] == events_after_response.json()["events"]
    assert events_after_response.json()["events"] == create_response.json()["live_events"]
    for forbidden in (
        str(config_path),
        "schema-validation-read-sentinel.invalid",
        "TEST_SCHEMA_VALIDATION_CONFIG_SECRET",
        "schema-validation-config-model",
        "SCHEMA_VALIDATION_CONFIG_BEARER",
        "TEST_SCHEMA_VALIDATION_ENV_OPENAI_KEY",
        "TEST_SCHEMA_VALIDATION_ENV_MEETING_KEY",
        "Bearer",
        "sk-",
    ):
        assert forbidden not in response.text
    assert "llm_schema_result" not in [
        event["event_type"] for event in events_after_response.json()["events"]
    ]
    assert "suggestion_card" not in [
        event["event_type"] for event in events_after_response.json()["events"]
    ]
    assert "suggestion_silenced" not in [
        event["event_type"] for event in events_after_response.json()["events"]
    ]


def test_asr_live_llm_schema_validation_dry_run_endpoint_reports_candidate_errors_without_creating_card():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(
            session_id="local_asr_schema_validation_dry_run_invalid_review"
        ),
    )
    invalid_candidate = _valid_schema_validation_candidate_response()
    invalid_candidate["usage"] = {}
    invalid_candidate["schema_result"] = "failed"
    invalid_candidate["latency_ms"] = 999
    events_before_response = client.get(
        "/live/asr/sessions/local_asr_schema_validation_dry_run_invalid_review/events"
    )

    response = client.post(
        "/live/asr/sessions/local_asr_schema_validation_dry_run_invalid_review/llm-schema-validation-dry-runs",
        json={
            "mode": "dry_run_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": invalid_candidate,
        },
    )
    events_after_response = client.get(
        "/live/asr/sessions/local_asr_schema_validation_dry_run_invalid_review/events"
    )

    assert create_response.status_code == 201
    assert events_before_response.status_code == 200
    assert response.status_code == 200
    body = response.json()
    assert body["validation_status"] == "failed"
    assert body["schema_validation_status"] == "dry_run_failed"
    assert body["schema_result_status"] == "not_generated"
    assert body["card_status"] == "not_created"
    assert body["llm_call_status"] == "not_called"
    assert body["safe_to_create_card"] is False
    assert body["candidate_response_preview"] == {
        "id": "card_dry_run_001",
        "type": "owner_gap",
        "schema_result": "failed",
        "show_or_silence_decision": "show",
        "status": "new",
    }
    assert body["validation_errors"] == [
        {
            "field": "usage.total_tokens",
            "code": "missing_required_field",
            "message": "suggestion card card_dry_run_001 missing usage.total_tokens",
        },
        {
            "field": "schema_result",
            "code": "blocking_schema_result",
            "message": "card_dry_run_001 schema_result failed blocks strong suggestion",
        },
        {
            "field": "latency_ms",
            "code": "inconsistent_latency",
            "message": "card_dry_run_001 latency_ms must equal card_created_at_ms - final_segment_at_ms",
        },
    ]
    assert events_after_response.status_code == 200
    assert events_before_response.json()["events"] == events_after_response.json()["events"]
    assert "llm_schema_result" not in [
        event["event_type"] for event in events_after_response.json()["events"]
    ]
    assert "suggestion_card" not in [
        event["event_type"] for event in events_after_response.json()["events"]
    ]


def test_asr_live_llm_schema_validation_dry_run_endpoint_rejects_non_integer_candidate_fields():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(
            session_id="local_asr_schema_validation_dry_run_integer_review"
        ),
    )
    path = (
        "/live/asr/sessions/local_asr_schema_validation_dry_run_integer_review"
        "/llm-schema-validation-dry-runs"
    )
    cases = [
        ("usage.total_tokens", {"usage": {"total_tokens": 1.9}}, "invalid_type"),
        ("usage.total_tokens", {"usage": {"total_tokens": True}}, "invalid_type"),
        ("usage.total_tokens", {"usage": {"total_tokens": "0"}}, "invalid_type"),
        ("usage.total_tokens", {"usage": {"total_tokens": -1}}, "invalid_value"),
        ("schema_result", {"schema_result": "maybe"}, "unsupported_schema_result"),
        ("final_segment_at_ms", {"final_segment_at_ms": 3500.5}, "invalid_type"),
        ("state_event_at_ms", {"state_event_at_ms": True}, "invalid_type"),
        ("card_created_at_ms", {"card_created_at_ms": "3700"}, "invalid_type"),
        ("latency_ms", {"latency_ms": 200.25}, "invalid_type"),
        ("latency_ms", {"latency_ms": -1}, "invalid_value"),
        ("state_event_at_ms", {"state_event_at_ms": 3499}, "invalid_time_order"),
        ("card_created_at_ms", {"card_created_at_ms": 3499}, "invalid_time_order"),
        ("latency_ms", {"latency_ms": 999}, "inconsistent_latency"),
    ]

    assert create_response.status_code == 201
    for expected_field, overrides, expected_code in cases:
        candidate = _valid_schema_validation_candidate_response()
        candidate.update(overrides)
        response = client.post(
            path,
            json={
                "mode": "dry_run_only",
                "request_id": (
                    "asr_llm_request_draft_"
                    "asr_suggestion_candidate_asr_state_event_asr_seg_001"
                ),
                "candidate_response": candidate,
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["validation_status"] == "failed"
        assert body["schema_validation_status"] == "dry_run_failed"
        assert body["card_status"] == "not_created"
        assert body["llm_call_status"] == "not_called"
        assert {
            "field": expected_field,
            "code": expected_code,
        } in [
            {
                "field": error["field"],
                "code": error["code"],
            }
            for error in body["validation_errors"]
        ]


def test_asr_live_llm_schema_validation_dry_run_endpoint_allows_blocking_schema_result_only_for_non_strong_cards():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(
            session_id="local_asr_schema_validation_dry_run_non_strong_review"
        ),
    )
    candidate = _valid_schema_validation_candidate_response()
    candidate["schema_result"] = "timeout"
    candidate["show_or_silence_decision"] = "silence"
    candidate["status"] = "dismissed"

    response = client.post(
        (
            "/live/asr/sessions/local_asr_schema_validation_dry_run_non_strong_review"
            "/llm-schema-validation-dry-runs"
        ),
        json={
            "mode": "dry_run_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": candidate,
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["validation_status"] == "passed"
    assert body["schema_validation_status"] == "dry_run_passed"
    assert body["validation_errors"] == []


def test_asr_live_llm_schema_validation_dry_run_endpoint_returns_404_for_unknown_request_id():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(
            session_id="local_asr_schema_validation_dry_run_unknown_request_review"
        ),
    )

    response = client.post(
        "/live/asr/sessions/local_asr_schema_validation_dry_run_unknown_request_review/llm-schema-validation-dry-runs",
        json={
            "mode": "dry_run_only",
            "request_id": "missing_request_id",
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 404
    assert (
        "LLM request draft not found for schema validation dry-run: missing_request_id"
        in response.text
    )


def test_asr_live_llm_schema_validation_dry_run_endpoint_reads_persisted_record_across_app_instances(
    tmp_path,
):
    first_client = TestClient(create_app(data_dir=tmp_path))
    create_response = first_client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(
            session_id="persisted_asr_schema_validation_dry_run_review"
        ),
    )

    second_client = TestClient(create_app(data_dir=tmp_path))
    response = second_client.post(
        "/live/asr/sessions/persisted_asr_schema_validation_dry_run_review/llm-schema-validation-dry-runs",
        json={
            "mode": "dry_run_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == "persisted_asr_schema_validation_dry_run_review"
    assert body["request_draft_event_id"] == "llm_request_draft:asr_state_event_asr_seg_001"
    assert body["validation_status"] == "passed"
    assert body["schema_validation_status"] == "dry_run_passed"


def test_asr_live_llm_schema_validation_dry_run_endpoint_returns_404_for_missing_session():
    client = TestClient(create_app())

    response = client.post(
        "/live/asr/sessions/missing_asr_review/llm-schema-validation-dry-runs",
        json={
            "mode": "dry_run_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert response.status_code == 404
    assert "ASR live session not found: missing_asr_review" in response.text


def test_asr_live_llm_schema_validation_dry_run_endpoint_rejects_request_shape_errors():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(
            session_id="local_asr_schema_validation_dry_run_shape_review"
        ),
    )
    path = (
        "/live/asr/sessions/local_asr_schema_validation_dry_run_shape_review"
        "/llm-schema-validation-dry-runs"
    )

    cases = [
        ([], "request body must be an object"),
        ({}, "missing mode"),
        (
            {
                "mode": 123,
                "request_id": "request",
                "candidate_response": {},
            },
            "mode must be a string",
        ),
        (
            {
                "mode": "dry_run_only",
                "request_id": 123,
                "candidate_response": {},
            },
            "request_id must be a string",
        ),
        (
            {
                "mode": "enabled",
                "request_id": "request",
                "candidate_response": {},
            },
            "unsupported schema validation mode: enabled",
        ),
        (
            {
                "mode": "dry_run_only",
                "candidate_response": {},
            },
            "missing request_id",
        ),
        (
            {
                "mode": "dry_run_only",
                "request_id": "request",
            },
            "missing candidate_response",
        ),
        (
            {
                "mode": "dry_run_only",
                "request_id": "request",
                "candidate_response": [],
            },
            "candidate_response must be an object",
        ),
        (
            {
                "mode": "dry_run_only",
                "request_id": "request",
                "candidate_response": {},
                "api_key": "ignored-test-value",
            },
            "extra fields are not permitted: api_key",
        ),
    ]
    assert create_response.status_code == 201
    for payload, expected_detail in cases:
        response = client.post(path, json=payload)
        assert response.status_code == 422
        assert expected_detail in response.text


def test_asr_live_llm_card_creation_policy_dry_run_endpoint_allows_candidate_without_creating_card(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "llm-gateway.local.json"
    config_path.write_text(
        (
            '{"base_url":"https://card-policy-read-sentinel.invalid",'
            '"api_key":"TEST_CARD_POLICY_CONFIG_SECRET",'
            '"model":"card-policy-config-model",'
            '"authorization":"Bearer CARD_POLICY_CONFIG_BEARER"}'
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MEETING_COPILOT_LLM_CONFIG", str(config_path))
    monkeypatch.setenv("OPENAI_API_KEY", "TEST_CARD_POLICY_ENV_OPENAI_KEY")
    monkeypatch.setenv(
        "MEETING_COPILOT_LLM_API_KEY",
        "TEST_CARD_POLICY_ENV_MEETING_KEY",
    )
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

    def is_llm_config_path(path) -> bool:
        return Path(path) == config_path

    def reject_llm_config_read_text(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError("card policy dry-run must not read config files")
        return original_read_text(path, *args, **kwargs)

    def reject_llm_config_read_bytes(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError("card policy dry-run must not read config bytes")
        return original_read_bytes(path, *args, **kwargs)

    def reject_llm_config_path_open(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError("card policy dry-run must not open config files")
        return original_path_open(path, *args, **kwargs)

    def reject_llm_config_builtin_open(file, *args, **kwargs):
        if is_llm_config_path(file):
            raise AssertionError("card policy dry-run must not open config files")
        return original_builtin_open(file, *args, **kwargs)

    def reject_llm_config_exists(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError("card policy dry-run must not check config existence")
        return original_path_exists(path, *args, **kwargs)

    def reject_llm_config_is_file(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError("card policy dry-run must not check config file type")
        return original_path_is_file(path, *args, **kwargs)

    def reject_llm_config_path_stat(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError("card policy dry-run must not stat config files")
        return original_path_stat(path, *args, **kwargs)

    def reject_llm_config_os_stat(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError("card policy dry-run must not stat config files")
        return original_os_stat(path, *args, **kwargs)

    def reject_llm_secret_getenv(key, *args, **kwargs):
        if key in {"OPENAI_API_KEY", "MEETING_COPILOT_LLM_API_KEY"}:
            raise AssertionError("card policy dry-run must not read env secrets")
        return original_getenv(key, *args, **kwargs)

    def reject_llm_secret_environ_get(key, *args, **kwargs):
        if key in {"OPENAI_API_KEY", "MEETING_COPILOT_LLM_API_KEY"}:
            raise AssertionError("card policy dry-run must not read env secrets")
        return original_environ_get(key, *args, **kwargs)

    def reject_llm_gateway_config_load(*args, **kwargs):
        raise AssertionError("card policy dry-run must not load llm gateway config")

    def reject_keychain_access(*args, **kwargs):
        raise AssertionError("card policy dry-run must not access keychain")

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
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(
            session_id="local_asr_card_policy_allowed_review"
        ),
    )
    events_before_response = client.get(
        "/live/asr/sessions/local_asr_card_policy_allowed_review/events"
    )

    response = client.post(
        "/live/asr/sessions/local_asr_card_policy_allowed_review/llm-card-creation-policy-dry-runs",
        json={
            "mode": "dry_run_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )
    events_after_response = client.get(
        "/live/asr/sessions/local_asr_card_policy_allowed_review/events"
    )

    assert create_response.status_code == 201
    assert events_before_response.status_code == 200
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "session_id": "local_asr_card_policy_allowed_review",
        "source": "live_asr_stream",
        "trace_kind": "live_event",
        "policy_mode": "dry_run_only",
        "policy_status": "allowed",
        "card_creation_policy_status": "dry_run_allowed",
        "schema_name": "SuggestionCardV1",
        "schema_validation_status": "dry_run_passed",
        "schema_result_status": "not_generated",
        "card_status": "not_created",
        "llm_call_status": "not_called",
        "credentials_status": "not_read",
        "config_source_status": "not_read",
        "cost_status": "not_estimated",
        "safe_to_create_card": False,
        "would_create_card_if_enabled": True,
        "would_silence_candidate_if_enabled": False,
        "request_id": (
            "asr_llm_request_draft_"
            "asr_suggestion_candidate_asr_state_event_asr_seg_001"
        ),
        "request_draft_event_id": "llm_request_draft:asr_state_event_asr_seg_001",
        "request_draft_sequence": 6,
        "target_candidate_id": (
            "asr_suggestion_candidate_asr_state_event_asr_seg_001"
        ),
        "target_type": "DecisionCandidate",
        "target_id": "asr_decision_asr_seg_001",
        "target_state_ref": "DecisionCandidate:asr_decision_asr_seg_001",
        "gap_rule_id": "release.rollback.owner.required",
        "source_event_ids": ["asr_state_event_asr_seg_001"],
        "evidence_span_ids": ["asr_ev_asr_seg_001"],
        "segment_batch": ["asr_seg_001"],
        "scheduler_policy_status": "queued",
        "scheduler_decision_reason": "state_change",
        "scheduler_candidate_event_type": "llm_candidate_queued",
        "validation_errors": [],
        "policy_errors": [],
        "policy_check_count": 13,
        "candidate_response_preview": {
            "id": "card_dry_run_001",
            "type": "owner_gap",
            "schema_result": "valid",
            "show_or_silence_decision": "show",
            "status": "new",
        },
        "block_reasons": [
            "card_creation_policy_dry_run_only",
            "card_lifecycle_disabled",
        ],
        "next_required_decisions": [
            "real_llm_response_parser",
            "llm_schema_result_event_lifecycle",
            "suggestion_card_persistence",
            "suggestion_silenced_lifecycle",
            "feedback_idempotency",
        ],
    }
    assert events_after_response.status_code == 200
    assert events_before_response.json()["events"] == events_after_response.json()["events"]
    assert events_after_response.json()["events"] == create_response.json()["live_events"]
    for forbidden in (
        str(config_path),
        "card-policy-read-sentinel.invalid",
        "TEST_CARD_POLICY_CONFIG_SECRET",
        "card-policy-config-model",
        "CARD_POLICY_CONFIG_BEARER",
        "TEST_CARD_POLICY_ENV_OPENAI_KEY",
        "TEST_CARD_POLICY_ENV_MEETING_KEY",
        "Bearer",
        "sk-",
    ):
        assert forbidden not in response.text
    assert "llm_schema_result" not in [
        event["event_type"] for event in events_after_response.json()["events"]
    ]
    assert "suggestion_card" not in [
        event["event_type"] for event in events_after_response.json()["events"]
    ]
    assert "suggestion_silenced" not in [
        event["event_type"] for event in events_after_response.json()["events"]
    ]


def test_asr_live_llm_card_creation_policy_dry_run_endpoint_blocks_schema_invalid_candidate():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(
            session_id="local_asr_card_policy_schema_invalid_review"
        ),
    )
    candidate = _valid_schema_validation_candidate_response()
    candidate["usage"] = {}
    candidate["schema_result"] = "failed"

    response = client.post(
        "/live/asr/sessions/local_asr_card_policy_schema_invalid_review/llm-card-creation-policy-dry-runs",
        json={
            "mode": "dry_run_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": candidate,
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["policy_status"] == "blocked"
    assert body["card_creation_policy_status"] == "dry_run_blocked"
    assert body["schema_validation_status"] == "dry_run_failed"
    assert body["would_create_card_if_enabled"] is False
    assert body["would_silence_candidate_if_enabled"] is True
    assert body["safe_to_create_card"] is False
    assert {
        "field": "schema_validation",
        "code": "schema_validation_failed",
    } in [
        {"field": error["field"], "code": error["code"]}
        for error in body["policy_errors"]
    ]
    assert {
        "field": "usage.total_tokens",
        "code": "missing_required_field",
    } in [
        {"field": error["field"], "code": error["code"]}
        for error in body["validation_errors"]
    ]


def test_asr_live_llm_card_creation_policy_dry_run_endpoint_reports_linkage_policy_errors():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(
            session_id="local_asr_card_policy_linkage_invalid_review"
        ),
    )
    candidate = _valid_schema_validation_candidate_response()
    candidate["gap_rule_id"] = "wrong.rule"
    candidate["evidence_span_ids"] = ["asr_ev_asr_seg_002"]
    candidate["segment_batch"] = ["asr_seg_002"]
    candidate["state_event_ids"] = ["asr_question_event_asr_seg_002"]
    candidate["state_refs"] = ["OpenQuestion:asr_question_asr_seg_002"]
    candidate["final_segment_at_ms"] = 7000
    candidate["state_event_at_ms"] = 7000
    candidate["card_created_at_ms"] = 7200
    candidate["latency_ms"] = 200

    response = client.post(
        "/live/asr/sessions/local_asr_card_policy_linkage_invalid_review/llm-card-creation-policy-dry-runs",
        json={
            "mode": "dry_run_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": candidate,
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["policy_status"] == "blocked"
    assert body["schema_validation_status"] == "dry_run_passed"
    assert body["would_create_card_if_enabled"] is False
    assert {
        (error["field"], error["code"])
        for error in body["policy_errors"]
    } >= {
        ("gap_rule_id", "request_linkage_mismatch"),
        ("evidence_span_ids", "request_linkage_mismatch"),
        ("segment_batch", "request_linkage_mismatch"),
        ("state_event_ids", "request_linkage_mismatch"),
        ("state_refs", "request_linkage_mismatch"),
    }


def test_asr_live_llm_card_creation_policy_dry_run_endpoint_blocks_stale_evidence_and_candidate_degradation():
    client = TestClient(create_app())
    stale_create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="local_asr_card_policy_stale_review"),
    )
    degraded_create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_with_low_confidence_candidate(
            session_id="local_asr_card_policy_degraded_review"
        ),
    )

    stale_response = client.post(
        "/live/asr/sessions/local_asr_card_policy_stale_review/llm-card-creation-policy-dry-runs",
        json={
            "mode": "dry_run_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )
    degraded_response = client.post(
        "/live/asr/sessions/local_asr_card_policy_degraded_review/llm-card-creation-policy-dry-runs",
        json={
            "mode": "dry_run_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert stale_create_response.status_code == 201
    assert degraded_create_response.status_code == 201
    assert stale_response.status_code == 200
    assert degraded_response.status_code == 200
    stale_body = stale_response.json()
    degraded_body = degraded_response.json()
    assert stale_body["policy_status"] == "blocked"
    assert {
        "field": "evidence_span_ids",
        "code": "stale_evidence",
    } in [
        {"field": error["field"], "code": error["code"]}
        for error in stale_body["policy_errors"]
    ]
    assert degraded_body["policy_status"] == "blocked"
    assert {
        "field": "candidate_degradation_reasons",
        "code": "candidate_quality_degraded",
    } in [
        {"field": error["field"], "code": error["code"]}
        for error in degraded_body["policy_errors"]
    ]


def test_asr_live_llm_card_creation_policy_dry_run_endpoint_blocks_cooldown_skipped_candidate():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(
            session_id="local_asr_card_policy_cooldown_skipped_review"
        ),
    )
    candidate = _valid_schema_validation_candidate_response()
    candidate.update(
        {
            "id": "card_dry_run_cooldown_001",
            "evidence_span_ids": ["asr_ev_asr_seg_001_rev1"],
            "state_refs": ["DecisionCandidate:asr_decision_asr_seg_001_rev1"],
            "state_event_ids": ["asr_state_event_asr_seg_001_rev1"],
            "segment_batch": ["asr_seg_001_rev1"],
            "final_segment_at_ms": 5200,
            "state_event_at_ms": 5200,
            "card_created_at_ms": 5400,
            "latency_ms": 200,
        }
    )

    response = client.post(
        (
            "/live/asr/sessions/local_asr_card_policy_cooldown_skipped_review"
            "/llm-card-creation-policy-dry-runs"
        ),
        json={
            "mode": "dry_run_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001_rev1"
            ),
            "candidate_response": candidate,
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["schema_validation_status"] == "dry_run_passed"
    assert body["policy_status"] == "blocked"
    assert body["card_creation_policy_status"] == "dry_run_blocked"
    assert body["scheduler_policy_status"] == "blocked_by_cooldown"
    assert body["scheduler_decision_reason"] == "cooldown"
    assert body["scheduler_candidate_event_type"] == "llm_candidate_skipped"
    assert body["would_create_card_if_enabled"] is False
    assert body["would_silence_candidate_if_enabled"] is True
    assert {
        "field": "scheduler_event_type",
        "code": "scheduler_candidate_not_queued",
    } in [
        {"field": error["field"], "code": error["code"]}
        for error in body["policy_errors"]
    ]
    assert "cooldown" in response.text


def test_asr_live_llm_card_creation_policy_dry_run_endpoint_blocks_timing_policy_errors():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(
            session_id="local_asr_card_policy_timing_invalid_review"
        ),
    )
    candidate = _valid_schema_validation_candidate_response()
    candidate["final_segment_at_ms"] = 3499
    candidate["state_event_at_ms"] = 3501
    candidate["card_created_at_ms"] = 40000
    candidate["latency_ms"] = 36501

    response = client.post(
        "/live/asr/sessions/local_asr_card_policy_timing_invalid_review/llm-card-creation-policy-dry-runs",
        json={
            "mode": "dry_run_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": candidate,
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["policy_status"] == "blocked"
    assert {
        (error["field"], error["code"])
        for error in body["policy_errors"]
    } >= {
        ("final_segment_at_ms", "segment_time_mismatch"),
        ("state_event_at_ms", "state_event_time_mismatch"),
        ("latency_ms", "strong_card_too_late"),
    }


def test_asr_live_llm_card_creation_policy_dry_run_endpoint_returns_404_for_unknown_request_id():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(
            session_id="local_asr_card_policy_unknown_request_review"
        ),
    )

    response = client.post(
        "/live/asr/sessions/local_asr_card_policy_unknown_request_review/llm-card-creation-policy-dry-runs",
        json={
            "mode": "dry_run_only",
            "request_id": "missing_request_id",
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 404
    assert (
        "LLM request draft not found for card creation policy dry-run: missing_request_id"
        in response.text
    )


def test_asr_live_llm_card_creation_policy_dry_run_endpoint_reads_persisted_record_across_app_instances(
    tmp_path,
):
    first_client = TestClient(create_app(data_dir=tmp_path))
    create_response = first_client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(
            session_id="persisted_asr_card_policy_dry_run_review"
        ),
    )

    second_client = TestClient(create_app(data_dir=tmp_path))
    response = second_client.post(
        "/live/asr/sessions/persisted_asr_card_policy_dry_run_review/llm-card-creation-policy-dry-runs",
        json={
            "mode": "dry_run_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == "persisted_asr_card_policy_dry_run_review"
    assert body["policy_status"] == "allowed"
    assert body["card_creation_policy_status"] == "dry_run_allowed"


def test_asr_live_llm_card_creation_policy_dry_run_endpoint_returns_404_for_missing_session():
    client = TestClient(create_app())

    response = client.post(
        "/live/asr/sessions/missing_asr_card_policy_review/llm-card-creation-policy-dry-runs",
        json={
            "mode": "dry_run_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert response.status_code == 404
    assert "ASR live session not found: missing_asr_card_policy_review" in response.text


def test_asr_live_llm_card_creation_policy_dry_run_endpoint_rejects_request_shape_errors():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(
            session_id="local_asr_card_policy_shape_review"
        ),
    )
    path = (
        "/live/asr/sessions/local_asr_card_policy_shape_review"
        "/llm-card-creation-policy-dry-runs"
    )

    cases = [
        ([], "request body must be an object"),
        ({}, "missing mode"),
        (
            {
                "mode": 123,
                "request_id": "request",
                "candidate_response": {},
            },
            "mode must be a string",
        ),
        (
            {
                "mode": "dry_run_only",
                "request_id": 123,
                "candidate_response": {},
            },
            "request_id must be a string",
        ),
        (
            {
                "mode": "enabled",
                "request_id": "request",
                "candidate_response": {},
            },
            "unsupported schema validation mode: enabled",
        ),
        (
            {
                "mode": "dry_run_only",
                "candidate_response": {},
            },
            "missing request_id",
        ),
        (
            {
                "mode": "dry_run_only",
                "request_id": "request",
            },
            "missing candidate_response",
        ),
        (
            {
                "mode": "dry_run_only",
                "request_id": "request",
                "candidate_response": [],
            },
            "candidate_response must be an object",
        ),
        (
            {
                "mode": "dry_run_only",
                "request_id": "request",
                "candidate_response": {},
                "api_key": "ignored-test-value",
            },
            "extra fields are not permitted: api_key",
        ),
    ]
    assert create_response.status_code == 201
    for payload, expected_detail in cases:
        response = client.post(path, json=payload)
        assert response.status_code == 422
        assert expected_detail in response.text


def test_asr_live_llm_card_lifecycle_preview_dry_run_endpoint_allows_card_preview_without_mutating_events(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "llm-gateway.local.json"
    config_path.write_text(
        (
            '{"base_url":"https://card-lifecycle-read-sentinel.invalid",'
            '"api_key":"TEST_CARD_LIFECYCLE_CONFIG_SECRET",'
            '"model":"card-lifecycle-config-model",'
            '"authorization":"Bearer CARD_LIFECYCLE_CONFIG_BEARER"}'
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MEETING_COPILOT_LLM_CONFIG", str(config_path))
    monkeypatch.setenv("OPENAI_API_KEY", "TEST_CARD_LIFECYCLE_ENV_OPENAI_KEY")
    monkeypatch.setenv(
        "MEETING_COPILOT_LLM_API_KEY",
        "TEST_CARD_LIFECYCLE_ENV_MEETING_KEY",
    )
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

    def is_llm_config_path(path) -> bool:
        return Path(path) == config_path

    def reject_llm_config_read_text(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError("card lifecycle preview must not read config files")
        return original_read_text(path, *args, **kwargs)

    def reject_llm_config_read_bytes(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError("card lifecycle preview must not read config bytes")
        return original_read_bytes(path, *args, **kwargs)

    def reject_llm_config_path_open(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError("card lifecycle preview must not open config files")
        return original_path_open(path, *args, **kwargs)

    def reject_llm_config_builtin_open(file, *args, **kwargs):
        if is_llm_config_path(file):
            raise AssertionError("card lifecycle preview must not open config files")
        return original_builtin_open(file, *args, **kwargs)

    def reject_llm_config_exists(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError("card lifecycle preview must not check config existence")
        return original_path_exists(path, *args, **kwargs)

    def reject_llm_config_is_file(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError("card lifecycle preview must not check config file type")
        return original_path_is_file(path, *args, **kwargs)

    def reject_llm_config_path_stat(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError("card lifecycle preview must not stat config files")
        return original_path_stat(path, *args, **kwargs)

    def reject_llm_config_os_stat(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError("card lifecycle preview must not stat config files")
        return original_os_stat(path, *args, **kwargs)

    def reject_llm_secret_getenv(key, *args, **kwargs):
        if key in {"OPENAI_API_KEY", "MEETING_COPILOT_LLM_API_KEY"}:
            raise AssertionError("card lifecycle preview must not read env secrets")
        return original_getenv(key, *args, **kwargs)

    def reject_llm_secret_environ_get(key, *args, **kwargs):
        if key in {"OPENAI_API_KEY", "MEETING_COPILOT_LLM_API_KEY"}:
            raise AssertionError("card lifecycle preview must not read env secrets")
        return original_environ_get(key, *args, **kwargs)

    def reject_llm_gateway_config_load(*args, **kwargs):
        raise AssertionError("card lifecycle preview must not load llm gateway config")

    def reject_keychain_access(*args, **kwargs):
        raise AssertionError("card lifecycle preview must not access keychain")

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
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(
            session_id="local_asr_card_lifecycle_allowed_review"
        ),
    )
    events_before_response = client.get(
        "/live/asr/sessions/local_asr_card_lifecycle_allowed_review/events"
    )

    response = client.post(
        "/live/asr/sessions/local_asr_card_lifecycle_allowed_review/llm-card-lifecycle-preview-dry-runs",
        json={
            "mode": "dry_run_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )
    events_after_response = client.get(
        "/live/asr/sessions/local_asr_card_lifecycle_allowed_review/events"
    )

    assert create_response.status_code == 201
    assert events_before_response.status_code == 200
    assert response.status_code == 200
    body = response.json()
    assert body["lifecycle_preview_status"] == "previewed"
    assert body["future_lifecycle_status"] == "would_create_card"
    assert body["schema_validation_status"] == "dry_run_passed"
    assert body["card_creation_policy_status"] == "dry_run_allowed"
    assert body["schema_result_status"] == "preview_only"
    assert body["card_status"] == "preview_only"
    assert body["silenced_status"] == "not_previewed"
    assert body["llm_call_status"] == "not_called"
    assert body["credentials_status"] == "not_read"
    assert body["config_source_status"] == "not_read"
    assert body["cost_status"] == "not_estimated"
    assert body["safe_to_append_events"] is False
    assert body["safe_to_create_card"] is False
    assert body["would_append_event_types_if_enabled"] == [
        "llm_schema_result",
        "suggestion_card",
    ]
    assert body["validation_errors"] == []
    assert body["policy_errors"] == []
    assert body["target_state_ref"] == "DecisionCandidate:asr_decision_asr_seg_001"
    assert [event["event_type"] for event in body["preview_events"]] == [
        "llm_schema_result",
        "suggestion_card",
    ]
    schema_preview, card_preview = body["preview_events"]
    assert schema_preview["preview_only"] is True
    assert schema_preview["would_append_if_enabled"] is True
    assert schema_preview["event_id"] == "preview:llm_schema_result:card_dry_run_001"
    assert schema_preview["at_ms"] == 3700
    assert schema_preview["payload"]["schema_result"] == "valid"
    assert schema_preview["payload"]["show_or_silence_decision"] == "show"
    assert schema_preview["payload"]["usage"] == {"total_tokens": 0}
    assert card_preview["preview_only"] is True
    assert card_preview["would_append_if_enabled"] is True
    assert card_preview["event_id"] == "preview:suggestion_card:card_dry_run_001"
    assert card_preview["payload"]["card"]["id"] == "card_dry_run_001"
    assert card_preview["payload"]["card"]["title"] == "确认回滚负责人"
    assert card_preview["payload"]["card"]["status"] == "new"
    assert events_after_response.status_code == 200
    assert events_before_response.json()["events"] == events_after_response.json()["events"]
    assert events_after_response.json()["events"] == create_response.json()["live_events"]
    for forbidden in (
        str(config_path),
        "card-lifecycle-read-sentinel.invalid",
        "TEST_CARD_LIFECYCLE_CONFIG_SECRET",
        "card-lifecycle-config-model",
        "CARD_LIFECYCLE_CONFIG_BEARER",
        "TEST_CARD_LIFECYCLE_ENV_OPENAI_KEY",
        "TEST_CARD_LIFECYCLE_ENV_MEETING_KEY",
        "Bearer",
        "sk-",
    ):
        assert forbidden not in response.text
    assert "llm_schema_result" not in [
        event["event_type"] for event in events_after_response.json()["events"]
    ]
    assert "suggestion_card" not in [
        event["event_type"] for event in events_after_response.json()["events"]
    ]
    assert "suggestion_silenced" not in [
        event["event_type"] for event in events_after_response.json()["events"]
    ]


def test_asr_live_llm_card_lifecycle_preview_dry_run_endpoint_silences_schema_invalid_candidate():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(
            session_id="local_asr_card_lifecycle_schema_invalid_review"
        ),
    )
    candidate = _valid_schema_validation_candidate_response()
    candidate["usage"] = {}
    candidate["schema_result"] = "failed"

    response = client.post(
        "/live/asr/sessions/local_asr_card_lifecycle_schema_invalid_review/llm-card-lifecycle-preview-dry-runs",
        json={
            "mode": "dry_run_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": candidate,
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["future_lifecycle_status"] == "would_silence_candidate"
    assert body["schema_validation_status"] == "dry_run_failed"
    assert body["card_creation_policy_status"] == "dry_run_blocked"
    assert body["card_status"] == "not_created"
    assert body["silenced_status"] == "preview_only"
    assert body["would_append_event_types_if_enabled"] == [
        "llm_schema_result",
        "suggestion_silenced",
    ]
    assert [event["event_type"] for event in body["preview_events"]] == [
        "llm_schema_result",
        "suggestion_silenced",
    ]
    silenced_preview = body["preview_events"][1]
    assert silenced_preview["payload"]["silence_reason"] == "schema_validation_failed"
    assert {
        "field": "schema_validation",
        "code": "schema_validation_failed",
    } in [
        {"field": error["field"], "code": error["code"]}
        for error in body["policy_errors"]
    ]


def test_asr_live_llm_card_lifecycle_preview_dry_run_endpoint_silences_policy_blocked_candidate():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="local_asr_card_lifecycle_policy_blocked_review"),
    )

    response = client.post(
        "/live/asr/sessions/local_asr_card_lifecycle_policy_blocked_review/llm-card-lifecycle-preview-dry-runs",
        json={
            "mode": "dry_run_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["future_lifecycle_status"] == "would_silence_candidate"
    assert body["schema_validation_status"] == "dry_run_passed"
    assert body["card_creation_policy_status"] == "dry_run_blocked"
    assert body["would_append_event_types_if_enabled"] == [
        "llm_schema_result",
        "suggestion_silenced",
    ]
    assert [event["event_type"] for event in body["preview_events"]] == [
        "llm_schema_result",
        "suggestion_silenced",
    ]
    assert body["preview_events"][1]["payload"]["silence_reason"] == (
        "card_creation_policy_blocked"
    )
    assert {
        "field": "evidence_span_ids",
        "code": "stale_evidence",
    } in [
        {"field": error["field"], "code": error["code"]}
        for error in body["policy_errors"]
    ]


def test_asr_live_llm_card_lifecycle_preview_dry_run_endpoint_silences_cooldown_skipped_candidate():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(
            session_id="local_asr_card_lifecycle_cooldown_skipped_review"
        ),
    )
    candidate = _valid_schema_validation_candidate_response()
    candidate.update(
        {
            "id": "card_dry_run_cooldown_001",
            "evidence_span_ids": ["asr_ev_asr_seg_001_rev1"],
            "state_refs": ["DecisionCandidate:asr_decision_asr_seg_001_rev1"],
            "state_event_ids": ["asr_state_event_asr_seg_001_rev1"],
            "segment_batch": ["asr_seg_001_rev1"],
            "final_segment_at_ms": 5200,
            "state_event_at_ms": 5200,
            "card_created_at_ms": 5400,
            "latency_ms": 200,
        }
    )

    response = client.post(
        (
            "/live/asr/sessions/local_asr_card_lifecycle_cooldown_skipped_review"
            "/llm-card-lifecycle-preview-dry-runs"
        ),
        json={
            "mode": "dry_run_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001_rev1"
            ),
            "candidate_response": candidate,
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["future_lifecycle_status"] == "would_silence_candidate"
    assert body["schema_validation_status"] == "dry_run_passed"
    assert body["card_creation_policy_status"] == "dry_run_blocked"
    assert body["scheduler_policy_status"] == "blocked_by_cooldown"
    assert body["scheduler_decision_reason"] == "cooldown"
    assert body["scheduler_candidate_event_type"] == "llm_candidate_skipped"
    assert body["would_append_event_types_if_enabled"] == [
        "llm_schema_result",
        "suggestion_silenced",
    ]
    assert [event["event_type"] for event in body["preview_events"]] == [
        "llm_schema_result",
        "suggestion_silenced",
    ]
    assert body["preview_events"][1]["payload"]["silence_reason"] == (
        "card_creation_policy_blocked"
    )
    assert {
        "field": "scheduler_event_type",
        "code": "scheduler_candidate_not_queued",
    } in [
        {"field": error["field"], "code": error["code"]}
        for error in body["policy_errors"]
    ]


def test_asr_live_llm_card_lifecycle_preview_dry_run_endpoint_returns_404_for_unknown_request_id():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(
            session_id="local_asr_card_lifecycle_unknown_request_review"
        ),
    )

    response = client.post(
        "/live/asr/sessions/local_asr_card_lifecycle_unknown_request_review/llm-card-lifecycle-preview-dry-runs",
        json={
            "mode": "dry_run_only",
            "request_id": "missing_request_id",
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 404
    assert (
        "LLM request draft not found for card lifecycle preview dry-run: missing_request_id"
        in response.text
    )


def test_asr_live_llm_card_lifecycle_preview_dry_run_endpoint_reads_persisted_record_across_app_instances(
    tmp_path,
):
    first_client = TestClient(create_app(data_dir=tmp_path))
    create_response = first_client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(
            session_id="persisted_asr_card_lifecycle_preview_review"
        ),
    )

    second_client = TestClient(create_app(data_dir=tmp_path))
    response = second_client.post(
        "/live/asr/sessions/persisted_asr_card_lifecycle_preview_review/llm-card-lifecycle-preview-dry-runs",
        json={
            "mode": "dry_run_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == "persisted_asr_card_lifecycle_preview_review"
    assert body["future_lifecycle_status"] == "would_create_card"
    assert body["preview_events"][1]["event_type"] == "suggestion_card"


def test_asr_live_llm_card_lifecycle_preview_dry_run_endpoint_returns_404_for_missing_session():
    client = TestClient(create_app())

    response = client.post(
        "/live/asr/sessions/missing_asr_card_lifecycle_review/llm-card-lifecycle-preview-dry-runs",
        json={
            "mode": "dry_run_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert response.status_code == 404
    assert "ASR live session not found: missing_asr_card_lifecycle_review" in response.text


def test_asr_live_llm_card_lifecycle_preview_dry_run_endpoint_rejects_request_shape_errors():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(
            session_id="local_asr_card_lifecycle_shape_review"
        ),
    )
    path = (
        "/live/asr/sessions/local_asr_card_lifecycle_shape_review"
        "/llm-card-lifecycle-preview-dry-runs"
    )

    cases = [
        ([], "request body must be an object"),
        ({}, "missing mode"),
        (
            {
                "mode": 123,
                "request_id": "request",
                "candidate_response": {},
            },
            "mode must be a string",
        ),
        (
            {
                "mode": "dry_run_only",
                "request_id": 123,
                "candidate_response": {},
            },
            "request_id must be a string",
        ),
        (
            {
                "mode": "enabled",
                "request_id": "request",
                "candidate_response": {},
            },
            "unsupported schema validation mode: enabled",
        ),
        (
            {
                "mode": "dry_run_only",
                "candidate_response": {},
            },
            "missing request_id",
        ),
        (
            {
                "mode": "dry_run_only",
                "request_id": "request",
            },
            "missing candidate_response",
        ),
        (
            {
                "mode": "dry_run_only",
                "request_id": "request",
                "candidate_response": [],
            },
            "candidate_response must be an object",
        ),
        (
            {
                "mode": "dry_run_only",
                "request_id": "request",
                "candidate_response": {},
                "api_key": "ignored-test-value",
            },
            "extra fields are not permitted: api_key",
        ),
    ]
    assert create_response.status_code == 201
    for payload, expected_detail in cases:
        response = client.post(path, json=payload)
        assert response.status_code == 422
        assert expected_detail in response.text


def test_asr_live_llm_card_lifecycle_append_preflight_dry_run_endpoint_allows_future_append_plan_without_mutating_events(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "llm-gateway.local.json"
    config_path.write_text(
        (
            '{"base_url":"https://append-preflight-read-sentinel.invalid",'
            '"api_key":"TEST_APPEND_PREFLIGHT_CONFIG_SECRET",'
            '"model":"append-preflight-config-model",'
            '"authorization":"Bearer APPEND_PREFLIGHT_CONFIG_BEARER"}'
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MEETING_COPILOT_LLM_CONFIG", str(config_path))
    monkeypatch.setenv("OPENAI_API_KEY", "TEST_APPEND_PREFLIGHT_ENV_OPENAI_KEY")
    monkeypatch.setenv(
        "MEETING_COPILOT_LLM_API_KEY",
        "TEST_APPEND_PREFLIGHT_ENV_MEETING_KEY",
    )
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

    def is_llm_config_path(path) -> bool:
        return Path(path) == config_path

    def reject_llm_config_read_text(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError("append preflight must not read config files")
        return original_read_text(path, *args, **kwargs)

    def reject_llm_config_read_bytes(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError("append preflight must not read config bytes")
        return original_read_bytes(path, *args, **kwargs)

    def reject_llm_config_path_open(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError("append preflight must not open config files")
        return original_path_open(path, *args, **kwargs)

    def reject_llm_config_builtin_open(file, *args, **kwargs):
        if is_llm_config_path(file):
            raise AssertionError("append preflight must not open config files")
        return original_builtin_open(file, *args, **kwargs)

    def reject_llm_config_exists(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError("append preflight must not check config existence")
        return original_path_exists(path, *args, **kwargs)

    def reject_llm_config_is_file(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError("append preflight must not check config file type")
        return original_path_is_file(path, *args, **kwargs)

    def reject_llm_config_path_stat(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError("append preflight must not stat config files")
        return original_path_stat(path, *args, **kwargs)

    def reject_llm_config_os_stat(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError("append preflight must not stat config files")
        return original_os_stat(path, *args, **kwargs)

    def reject_llm_secret_getenv(key, *args, **kwargs):
        if key in {"OPENAI_API_KEY", "MEETING_COPILOT_LLM_API_KEY"}:
            raise AssertionError("append preflight must not read env secrets")
        return original_getenv(key, *args, **kwargs)

    def reject_llm_secret_environ_get(key, *args, **kwargs):
        if key in {"OPENAI_API_KEY", "MEETING_COPILOT_LLM_API_KEY"}:
            raise AssertionError("append preflight must not read env secrets")
        return original_environ_get(key, *args, **kwargs)

    def reject_llm_gateway_config_load(*args, **kwargs):
        raise AssertionError("append preflight must not load llm gateway config")

    def reject_keychain_access(*args, **kwargs):
        raise AssertionError("append preflight must not access keychain")

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
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(
            session_id="local_asr_append_preflight_allowed_review"
        ),
    )
    events_before_response = client.get(
        "/live/asr/sessions/local_asr_append_preflight_allowed_review/events"
    )

    response = client.post(
        (
            "/live/asr/sessions/local_asr_append_preflight_allowed_review"
            "/llm-card-lifecycle-append-preflight-dry-runs"
        ),
        json={
            "mode": "dry_run_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )
    events_after_response = client.get(
        "/live/asr/sessions/local_asr_append_preflight_allowed_review/events"
    )

    assert create_response.status_code == 201
    assert events_before_response.status_code == 200
    assert response.status_code == 200
    body = response.json()
    assert body["append_preflight_mode"] == "dry_run_only"
    assert body["append_preflight_status"] == "allowed"
    assert body["lifecycle_preview_status"] == "previewed"
    assert body["future_lifecycle_status"] == "would_create_card"
    assert body["schema_validation_status"] == "dry_run_passed"
    assert body["card_creation_policy_status"] == "dry_run_allowed"
    assert body["llm_call_status"] == "not_called"
    assert body["credentials_status"] == "not_read"
    assert body["config_source_status"] == "not_read"
    assert body["cost_status"] == "not_estimated"
    assert body["safe_to_append_events"] is False
    assert body["safe_to_create_card"] is False
    assert body["append_errors"] == []
    assert body["existing_event_count"] == len(create_response.json()["live_events"])
    assert body["last_existing_sequence"] == max(
        event["sequence"] for event in create_response.json()["live_events"]
    )
    assert body["append_plan_count"] == 2
    assert body["would_append_event_types_if_enabled"] == [
        "llm_schema_result",
        "suggestion_card",
    ]
    append_plan = body["append_plan"]
    assert [item["event_type"] for item in append_plan] == [
        "llm_schema_result",
        "suggestion_card",
    ]
    assert [item["future_event_id"] for item in append_plan] == [
        "llm_schema_result:card_dry_run_001",
        "suggestion_card:card_dry_run_001",
    ]
    assert [item["preview_event_id"] for item in append_plan] == [
        "preview:llm_schema_result:card_dry_run_001",
        "preview:suggestion_card:card_dry_run_001",
    ]
    assert [item["would_append_sequence"] for item in append_plan] == [
        body["last_existing_sequence"] + 1,
        body["last_existing_sequence"] + 2,
    ]
    assert [item["would_append_after_sequence"] for item in append_plan] == [
        body["last_existing_sequence"],
        body["last_existing_sequence"] + 1,
    ]
    assert append_plan[0]["idempotency_key"] == (
        "live_asr_card_lifecycle_append:"
        "local_asr_append_preflight_allowed_review:"
        "asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001:"
        "llm_schema_result:card_dry_run_001"
    )
    assert append_plan[1]["idempotency_key"] == (
        "live_asr_card_lifecycle_append:"
        "local_asr_append_preflight_allowed_review:"
        "asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001:"
        "suggestion_card:card_dry_run_001"
    )
    assert {item["append_status"] for item in append_plan} == {
        "would_append_once_if_enabled"
    }
    assert {item["conflict_status"] for item in append_plan} == {"none"}
    assert all(item["preview_only"] is True for item in append_plan)
    assert all(item["would_append_if_enabled"] is True for item in append_plan)
    assert events_after_response.status_code == 200
    assert events_before_response.json()["events"] == events_after_response.json()["events"]
    assert events_after_response.json()["events"] == create_response.json()["live_events"]
    for forbidden in (
        str(config_path),
        "append-preflight-read-sentinel.invalid",
        "TEST_APPEND_PREFLIGHT_CONFIG_SECRET",
        "append-preflight-config-model",
        "APPEND_PREFLIGHT_CONFIG_BEARER",
        "TEST_APPEND_PREFLIGHT_ENV_OPENAI_KEY",
        "TEST_APPEND_PREFLIGHT_ENV_MEETING_KEY",
        "Bearer",
        "sk-",
    ):
        assert forbidden not in response.text
    assert "llm_schema_result" not in [
        event["event_type"] for event in events_after_response.json()["events"]
    ]
    assert "suggestion_card" not in [
        event["event_type"] for event in events_after_response.json()["events"]
    ]
    assert "suggestion_silenced" not in [
        event["event_type"] for event in events_after_response.json()["events"]
    ]


def test_asr_live_llm_card_lifecycle_append_preflight_dry_run_endpoint_allows_schema_invalid_silenced_plan():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(
            session_id="local_asr_append_preflight_schema_invalid_review"
        ),
    )
    candidate = _valid_schema_validation_candidate_response()
    candidate["usage"] = {}
    candidate["schema_result"] = "failed"

    response = client.post(
        (
            "/live/asr/sessions/local_asr_append_preflight_schema_invalid_review"
            "/llm-card-lifecycle-append-preflight-dry-runs"
        ),
        json={
            "mode": "dry_run_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": candidate,
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["append_preflight_status"] == "allowed"
    assert body["future_lifecycle_status"] == "would_silence_candidate"
    assert body["schema_validation_status"] == "dry_run_failed"
    assert body["card_creation_policy_status"] == "dry_run_blocked"
    assert body["append_errors"] == []
    assert body["would_append_event_types_if_enabled"] == [
        "llm_schema_result",
        "suggestion_silenced",
    ]
    assert [item["future_event_id"] for item in body["append_plan"]] == [
        "llm_schema_result:card_dry_run_001",
        "suggestion_silenced:card_dry_run_001",
    ]
    assert {
        "field": "schema_validation",
        "code": "schema_validation_failed",
    } in [
        {"field": error["field"], "code": error["code"]}
        for error in body["policy_errors"]
    ]


def test_asr_live_llm_card_lifecycle_append_preflight_dry_run_endpoint_allows_policy_blocked_silenced_plan():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="local_asr_append_preflight_policy_blocked_review"),
    )

    response = client.post(
        (
            "/live/asr/sessions/local_asr_append_preflight_policy_blocked_review"
            "/llm-card-lifecycle-append-preflight-dry-runs"
        ),
        json={
            "mode": "dry_run_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["append_preflight_status"] == "allowed"
    assert body["future_lifecycle_status"] == "would_silence_candidate"
    assert body["schema_validation_status"] == "dry_run_passed"
    assert body["card_creation_policy_status"] == "dry_run_blocked"
    assert [item["event_type"] for item in body["append_plan"]] == [
        "llm_schema_result",
        "suggestion_silenced",
    ]
    assert {
        "field": "evidence_span_ids",
        "code": "stale_evidence",
    } in [
        {"field": error["field"], "code": error["code"]}
        for error in body["policy_errors"]
    ]


def test_asr_live_llm_card_lifecycle_append_preflight_dry_run_endpoint_blocks_existing_event_or_idempotency_conflicts(
    tmp_path,
):
    first_client = TestClient(create_app(data_dir=tmp_path))
    create_response = first_client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(
            session_id="persisted_asr_append_preflight_conflict_review"
        ),
    )
    record_path = (
        tmp_path
        / "live_asr_sessions"
        / "persisted_asr_append_preflight_conflict_review.json"
    )
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["events"].append(
        {
            "id": "llm_schema_result:card_dry_run_001",
            "event_type": "llm_schema_result",
            "at_ms": 3700,
            "sequence": 999,
            "source": "live_asr_stream",
            "trace_kind": "live_event",
            "payload": {
                "card_id": "card_dry_run_001",
                "idempotency_key": (
                    "live_asr_card_lifecycle_append:"
                    "persisted_asr_append_preflight_conflict_review:"
                    "asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001:"
                    "llm_schema_result:card_dry_run_001"
                ),
            },
        }
    )
    record["events"].append(
        {
            "id": "existing:suggestion_card_conflict_marker",
            "event_type": "append_idempotency_marker",
            "at_ms": 3701,
            "sequence": 1000,
            "source": "live_asr_stream",
            "trace_kind": "live_event",
            "idempotency_key": (
                "live_asr_card_lifecycle_append:"
                "persisted_asr_append_preflight_conflict_review:"
                "asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001:"
                "suggestion_card:card_dry_run_001"
            ),
            "payload": {},
        }
    )
    record_path.write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )

    second_client = TestClient(create_app(data_dir=tmp_path))
    response = second_client.post(
        (
            "/live/asr/sessions/persisted_asr_append_preflight_conflict_review"
            "/llm-card-lifecycle-append-preflight-dry-runs"
        ),
        json={
            "mode": "dry_run_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["append_preflight_status"] == "blocked"
    assert body["safe_to_append_events"] is False
    assert body["existing_event_count"] == len(record["events"])
    assert body["last_existing_sequence"] == 1000
    assert [item["append_status"] for item in body["append_plan"]] == [
        "blocked_existing_event",
        "blocked_existing_idempotency_key",
    ]
    assert [item["conflict_status"] for item in body["append_plan"]] == [
        "existing_event_id",
        "existing_idempotency_key",
    ]
    assert body["append_errors"] == [
        {
            "field": "future_event_id",
            "code": "existing_event_id",
            "message": (
                "future event already exists: "
                "llm_schema_result:card_dry_run_001"
            ),
        },
        {
            "field": "idempotency_key",
            "code": "existing_idempotency_key",
            "message": (
                "future idempotency key already exists for event: "
                "suggestion_card:card_dry_run_001"
            ),
        },
    ]


def test_asr_live_llm_card_lifecycle_append_preflight_dry_run_endpoint_blocks_payload_idempotency_key_conflict(
    tmp_path,
):
    first_client = TestClient(create_app(data_dir=tmp_path))
    create_response = first_client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(
            session_id="persisted_asr_append_preflight_payload_key_review"
        ),
    )
    record_path = (
        tmp_path
        / "live_asr_sessions"
        / "persisted_asr_append_preflight_payload_key_review.json"
    )
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["events"].append(
        {
            "id": "existing:payload_idempotency_key_marker",
            "event_type": "append_idempotency_marker",
            "at_ms": 3700,
            "sequence": 999,
            "source": "live_asr_stream",
            "trace_kind": "live_event",
            "payload": {
                "idempotency_key": (
                    "live_asr_card_lifecycle_append:"
                    "persisted_asr_append_preflight_payload_key_review:"
                    "asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001:"
                    "suggestion_card:card_dry_run_001"
                )
            },
        }
    )
    record_path.write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )

    second_client = TestClient(create_app(data_dir=tmp_path))
    response = second_client.post(
        (
            "/live/asr/sessions/persisted_asr_append_preflight_payload_key_review"
            "/llm-card-lifecycle-append-preflight-dry-runs"
        ),
        json={
            "mode": "dry_run_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["append_preflight_status"] == "blocked"
    assert [item["append_status"] for item in body["append_plan"]] == [
        "would_append_once_if_enabled",
        "blocked_existing_idempotency_key",
    ]
    assert body["append_errors"] == [
        {
            "field": "idempotency_key",
            "code": "existing_idempotency_key",
            "message": (
                "future idempotency key already exists for event: "
                "suggestion_card:card_dry_run_001"
            ),
        }
    ]


def test_asr_live_llm_card_lifecycle_append_preflight_dry_run_endpoint_returns_404_for_unknown_request_id():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(
            session_id="local_asr_append_preflight_unknown_request_review"
        ),
    )

    response = client.post(
        (
            "/live/asr/sessions/local_asr_append_preflight_unknown_request_review"
            "/llm-card-lifecycle-append-preflight-dry-runs"
        ),
        json={
            "mode": "dry_run_only",
            "request_id": "missing_request_id",
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 404
    assert (
        "LLM request draft not found for card lifecycle preview dry-run: missing_request_id"
        in response.text
    )


def test_asr_live_llm_card_lifecycle_append_preflight_dry_run_endpoint_reads_persisted_record_across_app_instances(
    tmp_path,
):
    first_client = TestClient(create_app(data_dir=tmp_path))
    create_response = first_client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(
            session_id="persisted_asr_append_preflight_review"
        ),
    )

    second_client = TestClient(create_app(data_dir=tmp_path))
    response = second_client.post(
        (
            "/live/asr/sessions/persisted_asr_append_preflight_review"
            "/llm-card-lifecycle-append-preflight-dry-runs"
        ),
        json={
            "mode": "dry_run_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == "persisted_asr_append_preflight_review"
    assert body["append_preflight_status"] == "allowed"
    assert body["append_plan"][0]["idempotency_key"] == (
        "live_asr_card_lifecycle_append:"
        "persisted_asr_append_preflight_review:"
        "asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001:"
        "llm_schema_result:card_dry_run_001"
    )


def test_asr_live_llm_card_lifecycle_append_preflight_dry_run_endpoint_returns_404_for_missing_session():
    client = TestClient(create_app())

    response = client.post(
        (
            "/live/asr/sessions/missing_asr_append_preflight_review"
            "/llm-card-lifecycle-append-preflight-dry-runs"
        ),
        json={
            "mode": "dry_run_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert response.status_code == 404
    assert "ASR live session not found: missing_asr_append_preflight_review" in response.text


def test_asr_live_llm_card_lifecycle_append_preflight_dry_run_endpoint_rejects_request_shape_errors():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(
            session_id="local_asr_append_preflight_shape_review"
        ),
    )
    path = (
        "/live/asr/sessions/local_asr_append_preflight_shape_review"
        "/llm-card-lifecycle-append-preflight-dry-runs"
    )

    cases = [
        ([], "request body must be an object"),
        ({}, "missing mode"),
        (
            {
                "mode": 123,
                "request_id": "request",
                "candidate_response": {},
            },
            "mode must be a string",
        ),
        (
            {
                "mode": "dry_run_only",
                "request_id": 123,
                "candidate_response": {},
            },
            "request_id must be a string",
        ),
        (
            {
                "mode": "enabled",
                "request_id": "request",
                "candidate_response": {},
            },
            "unsupported schema validation mode: enabled",
        ),
        (
            {
                "mode": "dry_run_only",
                "candidate_response": {},
            },
            "missing request_id",
        ),
        (
            {
                "mode": "dry_run_only",
                "request_id": "request",
            },
            "missing candidate_response",
        ),
        (
            {
                "mode": "dry_run_only",
                "request_id": "request",
                "candidate_response": [],
            },
            "candidate_response must be an object",
        ),
        (
            {
                "mode": "dry_run_only",
                "request_id": "request",
                "candidate_response": {},
                "api_key": "ignored-test-value",
            },
            "extra fields are not permitted: api_key",
        ),
    ]
    assert create_response.status_code == 201
    for payload, expected_detail in cases:
        response = client.post(path, json=payload)
        assert response.status_code == 422
        assert expected_detail in response.text


def test_asr_live_llm_card_lifecycle_append_runs_disabled_endpoint_returns_skipped_runs_without_mutating_events(
    monkeypatch,
    tmp_path,
):
    forbidden_values = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "append_run_disabled",
    )
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(
            session_id="local_asr_append_run_disabled_review"
        ),
    )
    events_before_response = client.get(
        "/live/asr/sessions/local_asr_append_run_disabled_review/events"
    )

    response = client.post(
        (
            "/live/asr/sessions/local_asr_append_run_disabled_review"
            "/llm-card-lifecycle-append-runs"
        ),
        json={
            "mode": "disabled",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )
    events_after_response = client.get(
        "/live/asr/sessions/local_asr_append_run_disabled_review/events"
    )

    assert create_response.status_code == 201
    assert events_before_response.status_code == 200
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == "local_asr_append_run_disabled_review"
    assert body["source"] == "live_asr_stream"
    assert body["trace_kind"] == "live_event"
    assert body["append_run_mode"] == "disabled"
    assert body["append_run_status"] == "skipped"
    assert body["append_preflight_mode"] == "dry_run_only"
    assert body["append_preflight_status"] == "allowed"
    assert body["lifecycle_preview_status"] == "previewed"
    assert body["future_lifecycle_status"] == "would_create_card"
    assert body["schema_validation_status"] == "dry_run_passed"
    assert body["card_creation_policy_status"] == "dry_run_allowed"
    assert body["llm_call_status"] == "not_called"
    assert body["credentials_status"] == "not_read"
    assert body["config_source_status"] == "not_read"
    assert body["cost_status"] == "not_estimated"
    assert body["safe_to_append_events"] is False
    assert body["safe_to_create_card"] is False
    assert body["append_errors"] == []
    assert body["append_plan_count"] == 2
    assert body["append_run_count"] == 2
    assert body["would_append_event_types_if_enabled"] == [
        "llm_schema_result",
        "suggestion_card",
    ]
    assert [item["event_type"] for item in body["append_plan"]] == [
        "llm_schema_result",
        "suggestion_card",
    ]
    runs = body["append_runs"]
    assert [run["event_type"] for run in runs] == [
        "llm_schema_result",
        "suggestion_card",
    ]
    assert [run["future_event_id"] for run in runs] == [
        "llm_schema_result:card_dry_run_001",
        "suggestion_card:card_dry_run_001",
    ]
    assert [run["preview_event_id"] for run in runs] == [
        "preview:llm_schema_result:card_dry_run_001",
        "preview:suggestion_card:card_dry_run_001",
    ]
    assert [run["run_id"] for run in runs] == [
        "asr_card_lifecycle_append_run_disabled_llm_schema_result_card_dry_run_001",
        "asr_card_lifecycle_append_run_disabled_suggestion_card_card_dry_run_001",
    ]
    assert [run["idempotency_key"] for run in runs] == [
        (
            "live_asr_card_lifecycle_append_run:disabled:"
            "local_asr_append_run_disabled_review:"
            "asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001:"
            "llm_schema_result:card_dry_run_001"
        ),
        (
            "live_asr_card_lifecycle_append_run:disabled:"
            "local_asr_append_run_disabled_review:"
            "asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001:"
            "suggestion_card:card_dry_run_001"
        ),
    ]
    assert [run["preflight_idempotency_key"] for run in runs] == [
        item["idempotency_key"] for item in body["append_plan"]
    ]
    assert [run["would_append_sequence"] for run in runs] == [
        body["last_existing_sequence"] + 1,
        body["last_existing_sequence"] + 2,
    ]
    assert [run["would_append_after_sequence"] for run in runs] == [
        body["last_existing_sequence"],
        body["last_existing_sequence"] + 1,
    ]
    assert {run["run_status"] for run in runs} == {"skipped"}
    assert {run["skip_reason"] for run in runs} == {"event_append_disabled"}
    assert {run["preflight_append_status"] for run in runs} == {
        "would_append_once_if_enabled"
    }
    assert {run["preflight_conflict_status"] for run in runs} == {"none"}
    assert {run["llm_call_status"] for run in runs} == {"not_called"}
    assert {run["credentials_status"] for run in runs} == {"not_read"}
    assert {run["cost_status"] for run in runs} == {"not_estimated"}
    assert {run["event_append_status"] for run in runs} == {"not_appended"}
    assert {run["idempotency_store_status"] for run in runs} == {"not_written"}
    assert {run["safe_to_append_event"] for run in runs} == {False}
    assert body["block_reasons"] == [
        "append_run_disabled",
        "event_mutation_disabled",
    ]
    assert "enabled_card_lifecycle_mutation" in body["next_required_decisions"]
    assert events_after_response.status_code == 200
    assert events_before_response.json()["events"] == events_after_response.json()["events"]
    assert events_after_response.json()["events"] == create_response.json()["live_events"]
    for forbidden in forbidden_values:
        assert forbidden not in response.text
    assert "llm_schema_result" not in [
        event["event_type"] for event in events_after_response.json()["events"]
    ]
    assert "suggestion_card" not in [
        event["event_type"] for event in events_after_response.json()["events"]
    ]
    assert "suggestion_silenced" not in [
        event["event_type"] for event in events_after_response.json()["events"]
    ]


def test_asr_live_llm_card_lifecycle_append_runs_disabled_endpoint_skips_schema_invalid_silenced_runs():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(
            session_id="local_asr_append_run_schema_invalid_review"
        ),
    )
    candidate = _valid_schema_validation_candidate_response()
    candidate["usage"] = {}
    candidate["schema_result"] = "failed"

    response = client.post(
        (
            "/live/asr/sessions/local_asr_append_run_schema_invalid_review"
            "/llm-card-lifecycle-append-runs"
        ),
        json={
            "mode": "disabled",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": candidate,
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["append_run_mode"] == "disabled"
    assert body["append_run_status"] == "skipped"
    assert body["append_preflight_status"] == "allowed"
    assert body["future_lifecycle_status"] == "would_silence_candidate"
    assert body["schema_validation_status"] == "dry_run_failed"
    assert body["card_creation_policy_status"] == "dry_run_blocked"
    assert body["append_errors"] == []
    assert body["would_append_event_types_if_enabled"] == [
        "llm_schema_result",
        "suggestion_silenced",
    ]
    assert [run["event_type"] for run in body["append_runs"]] == [
        "llm_schema_result",
        "suggestion_silenced",
    ]
    assert {run["skip_reason"] for run in body["append_runs"]} == {
        "event_append_disabled"
    }
    assert {
        "field": "schema_validation",
        "code": "schema_validation_failed",
    } in [
        {"field": error["field"], "code": error["code"]}
        for error in body["policy_errors"]
    ]


def test_asr_live_llm_card_lifecycle_append_runs_disabled_endpoint_skips_policy_blocked_silenced_runs():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="local_asr_append_run_policy_blocked_review"),
    )

    response = client.post(
        (
            "/live/asr/sessions/local_asr_append_run_policy_blocked_review"
            "/llm-card-lifecycle-append-runs"
        ),
        json={
            "mode": "disabled",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["append_preflight_status"] == "allowed"
    assert body["future_lifecycle_status"] == "would_silence_candidate"
    assert body["schema_validation_status"] == "dry_run_passed"
    assert body["card_creation_policy_status"] == "dry_run_blocked"
    assert [run["event_type"] for run in body["append_runs"]] == [
        "llm_schema_result",
        "suggestion_silenced",
    ]
    assert {run["run_status"] for run in body["append_runs"]} == {"skipped"}
    assert {
        "field": "evidence_span_ids",
        "code": "stale_evidence",
    } in [
        {"field": error["field"], "code": error["code"]}
        for error in body["policy_errors"]
    ]


def test_asr_live_llm_card_lifecycle_append_runs_disabled_endpoint_preserves_preflight_conflicts(
    tmp_path,
):
    first_client = TestClient(create_app(data_dir=tmp_path))
    create_response = first_client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(
            session_id="persisted_asr_append_run_conflict_review"
        ),
    )
    record_path = (
        tmp_path
        / "live_asr_sessions"
        / "persisted_asr_append_run_conflict_review.json"
    )
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["events"].append(
        {
            "id": "llm_schema_result:card_dry_run_001",
            "event_type": "llm_schema_result",
            "at_ms": 3700,
            "sequence": 999,
            "source": "live_asr_stream",
            "trace_kind": "live_event",
            "payload": {
                "card_id": "card_dry_run_001",
                "idempotency_key": (
                    "live_asr_card_lifecycle_append:"
                    "persisted_asr_append_run_conflict_review:"
                    "asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001:"
                    "llm_schema_result:card_dry_run_001"
                ),
            },
        }
    )
    record["events"].append(
        {
            "id": "existing:suggestion_card_conflict_marker",
            "event_type": "append_idempotency_marker",
            "at_ms": 3701,
            "sequence": 1000,
            "source": "live_asr_stream",
            "trace_kind": "live_event",
            "idempotency_key": (
                "live_asr_card_lifecycle_append:"
                "persisted_asr_append_run_conflict_review:"
                "asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001:"
                "suggestion_card:card_dry_run_001"
            ),
            "payload": {},
        }
    )
    record_path.write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )

    second_client = TestClient(create_app(data_dir=tmp_path))
    events_before_response = second_client.get(
        "/live/asr/sessions/persisted_asr_append_run_conflict_review/events"
    )
    response = second_client.post(
        (
            "/live/asr/sessions/persisted_asr_append_run_conflict_review"
            "/llm-card-lifecycle-append-runs"
        ),
        json={
            "mode": "disabled",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )
    events_after_response = second_client.get(
        "/live/asr/sessions/persisted_asr_append_run_conflict_review/events"
    )

    assert create_response.status_code == 201
    assert events_before_response.status_code == 200
    assert response.status_code == 200
    body = response.json()
    assert body["append_run_status"] == "skipped"
    assert body["append_preflight_status"] == "blocked"
    assert body["safe_to_append_events"] is False
    assert body["append_errors"] == [
        {
            "field": "future_event_id",
            "code": "existing_event_id",
            "message": (
                "future event already exists: "
                "llm_schema_result:card_dry_run_001"
            ),
        },
        {
            "field": "idempotency_key",
            "code": "existing_idempotency_key",
            "message": (
                "future idempotency key already exists for event: "
                "suggestion_card:card_dry_run_001"
            ),
        },
    ]
    assert [run["preflight_append_status"] for run in body["append_runs"]] == [
        "blocked_existing_event",
        "blocked_existing_idempotency_key",
    ]
    assert [run["preflight_conflict_status"] for run in body["append_runs"]] == [
        "existing_event_id",
        "existing_idempotency_key",
    ]
    assert {run["skip_reason"] for run in body["append_runs"]} == {
        "append_preflight_blocked"
    }
    assert events_before_response.json()["events"] == events_after_response.json()["events"]


def test_asr_live_llm_card_lifecycle_append_runs_disabled_endpoint_returns_404_for_unknown_request_id():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(
            session_id="local_asr_append_run_unknown_request_review"
        ),
    )

    response = client.post(
        (
            "/live/asr/sessions/local_asr_append_run_unknown_request_review"
            "/llm-card-lifecycle-append-runs"
        ),
        json={
            "mode": "disabled",
            "request_id": "missing_request_id",
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 404
    assert (
        "LLM request draft not found for card lifecycle preview dry-run: missing_request_id"
        in response.text
    )


def test_asr_live_llm_card_lifecycle_append_runs_disabled_endpoint_reads_persisted_record_across_app_instances(
    tmp_path,
):
    first_client = TestClient(create_app(data_dir=tmp_path))
    create_response = first_client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(
            session_id="persisted_asr_append_run_disabled_review"
        ),
    )

    second_client = TestClient(create_app(data_dir=tmp_path))
    response = second_client.post(
        (
            "/live/asr/sessions/persisted_asr_append_run_disabled_review"
            "/llm-card-lifecycle-append-runs"
        ),
        json={
            "mode": "disabled",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == "persisted_asr_append_run_disabled_review"
    assert body["append_run_mode"] == "disabled"
    assert body["append_preflight_status"] == "allowed"
    assert body["append_run_count"] == 2
    assert body["append_runs"][0]["idempotency_key"] == (
        "live_asr_card_lifecycle_append_run:disabled:"
        "persisted_asr_append_run_disabled_review:"
        "asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001:"
        "llm_schema_result:card_dry_run_001"
    )


def test_asr_live_llm_card_lifecycle_append_runs_disabled_endpoint_returns_404_for_missing_session():
    client = TestClient(create_app())

    response = client.post(
        (
            "/live/asr/sessions/missing_asr_append_run_review"
            "/llm-card-lifecycle-append-runs"
        ),
        json={
            "mode": "disabled",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert response.status_code == 404
    assert "ASR live session not found: missing_asr_append_run_review" in response.text


def test_asr_live_llm_card_lifecycle_append_runs_disabled_endpoint_rejects_request_shape_errors():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(
            session_id="local_asr_append_run_shape_review"
        ),
    )
    path = (
        "/live/asr/sessions/local_asr_append_run_shape_review"
        "/llm-card-lifecycle-append-runs"
    )

    cases = [
        ([], "request body must be an object"),
        ({}, "missing mode"),
        (
            {
                "mode": 123,
                "request_id": "request",
                "candidate_response": {},
            },
            "mode must be a string",
        ),
        (
            {
                "mode": "disabled",
                "request_id": 123,
                "candidate_response": {},
            },
            "request_id must be a string",
        ),
        (
            {
                "mode": "enabled",
                "request_id": "request",
                "candidate_response": {},
            },
            "unsupported card lifecycle append run mode: enabled",
        ),
        (
            {
                "mode": " disabled ",
                "request_id": "request",
                "candidate_response": {},
            },
            "unsupported card lifecycle append run mode:  disabled ",
        ),
        (
            {
                "mode": "disabled",
                "candidate_response": {},
            },
            "missing request_id",
        ),
        (
            {
                "mode": "disabled",
                "request_id": "request",
            },
            "missing candidate_response",
        ),
        (
            {
                "mode": "disabled",
                "request_id": "request",
                "candidate_response": [],
            },
            "candidate_response must be an object",
        ),
        (
            {
                "mode": "disabled",
                "request_id": "request",
                "candidate_response": {},
                "api_key": "ignored-test-value",
            },
            "extra fields are not permitted: api_key",
        ),
    ]
    assert create_response.status_code == 201
    for payload, expected_detail in cases:
        response = client.post(path, json=payload)
        assert response.status_code == 422
        assert expected_detail in response.text


def test_card_lifecycle_append_run_id_token_avoids_punctuation_collisions():
    slash_token = app_module._run_id_token("llm_schema_result:a/b")
    colon_token = app_module._run_id_token("llm_schema_result:a:b")

    assert slash_token != colon_token
    assert slash_token == "llm_schema_result_a_2f_b"
    assert colon_token == "llm_schema_result_a_b"


def test_asr_live_llm_card_lifecycle_append_repository_dry_run_endpoint_returns_repository_contract_without_mutating_events(
    monkeypatch,
    tmp_path,
):
    forbidden_values = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "append_repository",
    )
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(
            session_id="local_asr_append_repository_allowed_review"
        ),
    )
    events_before_response = client.get(
        "/live/asr/sessions/local_asr_append_repository_allowed_review/events"
    )

    response = client.post(
        (
            "/live/asr/sessions/local_asr_append_repository_allowed_review"
            "/llm-card-lifecycle-append-repository-dry-runs"
        ),
        json={
            "mode": "dry_run_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )
    events_after_response = client.get(
        "/live/asr/sessions/local_asr_append_repository_allowed_review/events"
    )

    assert create_response.status_code == 201
    assert events_before_response.status_code == 200
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == "local_asr_append_repository_allowed_review"
    assert body["source"] == "live_asr_stream"
    assert body["trace_kind"] == "live_event"
    assert body["repository_dry_run_mode"] == "dry_run_only"
    assert body["repository_dry_run_status"] == "would_append_if_enabled"
    assert body["append_run_status"] == "skipped"
    assert body["append_preflight_status"] == "allowed"
    assert body["future_lifecycle_status"] == "would_create_card"
    assert body["schema_validation_status"] == "dry_run_passed"
    assert body["card_creation_policy_status"] == "dry_run_allowed"
    assert body["safe_to_append_events"] is False
    assert body["safe_to_create_card"] is False
    assert body["event_append_status"] == "not_appended"
    assert body["idempotency_store_status"] == "not_written"
    assert body["append_errors"] == []
    assert body["append_plan_count"] == 2
    assert body["append_run_count"] == 2
    assert body["repository_append_count"] == 2
    assert body["would_append_event_types_if_enabled"] == [
        "llm_schema_result",
        "suggestion_card",
    ]
    results = body["repository_results"]
    assert [result["event_type"] for result in results] == [
        "llm_schema_result",
        "suggestion_card",
    ]
    assert [result["future_event_id"] for result in results] == [
        "llm_schema_result:card_dry_run_001",
        "suggestion_card:card_dry_run_001",
    ]
    assert [result["repository_result_id"] for result in results] == [
        "asr_card_lifecycle_repository_dry_run_llm_schema_result_card_dry_run_001",
        "asr_card_lifecycle_repository_dry_run_suggestion_card_card_dry_run_001",
    ]
    assert [result["repository_idempotency_key"] for result in results] == [
        (
            "live_asr_card_lifecycle_repository_dry_run:"
            "local_asr_append_repository_allowed_review:"
            "asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001:"
            "llm_schema_result:card_dry_run_001"
        ),
        (
            "live_asr_card_lifecycle_repository_dry_run:"
            "local_asr_append_repository_allowed_review:"
            "asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001:"
            "suggestion_card:card_dry_run_001"
        ),
    ]
    assert [result["idempotency_key"] for result in results] == [
        item["idempotency_key"] for item in body["append_plan"]
    ]
    assert [result["would_append_sequence"] for result in results] == [
        body["last_existing_sequence"] + 1,
        body["last_existing_sequence"] + 2,
    ]
    assert {result["repository_result_status"] for result in results} == {
        "would_append_if_enabled"
    }
    assert {result["preflight_append_status"] for result in results} == {
        "would_append_once_if_enabled"
    }
    assert {result["preflight_conflict_status"] for result in results} == {"none"}
    assert {result["event_append_status"] for result in results} == {"not_appended"}
    assert {result["idempotency_store_status"] for result in results} == {
        "not_written"
    }
    assert {result["repository_write_status"] for result in results} == {
        "dry_run_only"
    }
    assert {result["safe_to_append_event"] for result in results} == {False}
    assert body["block_reasons"] == [
        "repository_append_dry_run_only",
        "event_mutation_disabled",
    ]
    assert "repository_append_transaction" in body["next_required_decisions"]
    assert events_after_response.status_code == 200
    assert events_before_response.json()["events"] == events_after_response.json()["events"]
    assert events_after_response.json()["events"] == create_response.json()["live_events"]
    for forbidden in forbidden_values:
        assert forbidden not in response.text
    assert "llm_schema_result" not in [
        event["event_type"] for event in events_after_response.json()["events"]
    ]
    assert "suggestion_card" not in [
        event["event_type"] for event in events_after_response.json()["events"]
    ]
    assert "suggestion_silenced" not in [
        event["event_type"] for event in events_after_response.json()["events"]
    ]


def test_asr_live_llm_card_lifecycle_append_repository_dry_run_endpoint_uses_collision_resistant_repository_identifiers():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(
            session_id="local_asr_append_repository_identifier_review"
        ),
    )
    request_id = (
        "asr_llm_request_draft_"
        "asr_suggestion_candidate_asr_state_event_asr_seg_001"
    )

    colon_candidate = _valid_schema_validation_candidate_response()
    colon_candidate["id"] = "card:dry_run:001"
    underscore_candidate = _valid_schema_validation_candidate_response()
    underscore_candidate["id"] = "card_dry_run_001"

    colon_response = client.post(
        (
            "/live/asr/sessions/local_asr_append_repository_identifier_review"
            "/llm-card-lifecycle-append-repository-dry-runs"
        ),
        json={
            "mode": "dry_run_only",
            "request_id": request_id,
            "candidate_response": colon_candidate,
        },
    )
    underscore_response = client.post(
        (
            "/live/asr/sessions/local_asr_append_repository_identifier_review"
            "/llm-card-lifecycle-append-repository-dry-runs"
        ),
        json={
            "mode": "dry_run_only",
            "request_id": request_id,
            "candidate_response": underscore_candidate,
        },
    )

    assert create_response.status_code == 201
    assert colon_response.status_code == 200
    assert underscore_response.status_code == 200
    colon_results = colon_response.json()["repository_results"]
    underscore_results = underscore_response.json()["repository_results"]
    assert [result["repository_result_id"] for result in colon_results] != [
        result["repository_result_id"] for result in underscore_results
    ]
    assert [result["repository_idempotency_key"] for result in colon_results] != [
        result["repository_idempotency_key"] for result in underscore_results
    ]
    assert all(
        "card%3Adry_run%3A001" in result["repository_result_id"]
        for result in colon_results
    )
    assert all(
        "card%3Adry_run%3A001" in result["repository_idempotency_key"]
        for result in colon_results
    )


def test_asr_live_llm_card_lifecycle_append_repository_dry_run_endpoint_returns_schema_invalid_silenced_results(
    monkeypatch,
    tmp_path,
):
    forbidden_values = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "append_repository_schema_invalid",
    )
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(
            session_id="local_asr_append_repository_schema_invalid_review"
        ),
    )
    candidate = _valid_schema_validation_candidate_response()
    candidate["usage"] = {}
    candidate["schema_result"] = "failed"

    response = client.post(
        (
            "/live/asr/sessions/local_asr_append_repository_schema_invalid_review"
            "/llm-card-lifecycle-append-repository-dry-runs"
        ),
        json={
            "mode": "dry_run_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": candidate,
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["repository_dry_run_status"] == "would_append_if_enabled"
    assert body["future_lifecycle_status"] == "would_silence_candidate"
    assert body["schema_validation_status"] == "dry_run_failed"
    assert body["card_creation_policy_status"] == "dry_run_blocked"
    assert [result["event_type"] for result in body["repository_results"]] == [
        "llm_schema_result",
        "suggestion_silenced",
    ]
    assert {
        "field": "schema_validation",
        "code": "schema_validation_failed",
    } in [
        {"field": error["field"], "code": error["code"]}
        for error in body["policy_errors"]
    ]
    for forbidden in forbidden_values:
        assert forbidden not in response.text


def test_asr_live_llm_card_lifecycle_append_repository_dry_run_endpoint_returns_policy_blocked_silenced_results(
    monkeypatch,
    tmp_path,
):
    forbidden_values = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "append_repository_policy_blocked",
    )
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(
            session_id="local_asr_append_repository_policy_blocked_review"
        ),
    )

    response = client.post(
        (
            "/live/asr/sessions/local_asr_append_repository_policy_blocked_review"
            "/llm-card-lifecycle-append-repository-dry-runs"
        ),
        json={
            "mode": "dry_run_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["repository_dry_run_status"] == "would_append_if_enabled"
    assert body["future_lifecycle_status"] == "would_silence_candidate"
    assert body["schema_validation_status"] == "dry_run_passed"
    assert body["card_creation_policy_status"] == "dry_run_blocked"
    assert [result["event_type"] for result in body["repository_results"]] == [
        "llm_schema_result",
        "suggestion_silenced",
    ]
    assert {
        "field": "evidence_span_ids",
        "code": "stale_evidence",
    } in [
        {"field": error["field"], "code": error["code"]}
        for error in body["policy_errors"]
    ]
    for forbidden in forbidden_values:
        assert forbidden not in response.text


def test_asr_live_llm_card_lifecycle_append_repository_dry_run_endpoint_preserves_preflight_conflicts(
    monkeypatch,
    tmp_path,
):
    forbidden_values = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "append_repository_conflict",
    )
    first_client = TestClient(create_app(data_dir=tmp_path))
    create_response = first_client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(
            session_id="persisted_asr_append_repository_conflict_review"
        ),
    )
    record_path = (
        tmp_path
        / "live_asr_sessions"
        / "persisted_asr_append_repository_conflict_review.json"
    )
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["events"].append(
        {
            "id": "llm_schema_result:card_dry_run_001",
            "event_type": "llm_schema_result",
            "at_ms": 3700,
            "sequence": 999,
            "source": "live_asr_stream",
            "trace_kind": "live_event",
            "payload": {
                "card_id": "card_dry_run_001",
                "idempotency_key": (
                    "live_asr_card_lifecycle_append:"
                    "persisted_asr_append_repository_conflict_review:"
                    "asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001:"
                    "llm_schema_result:card_dry_run_001"
                ),
            },
        }
    )
    record["events"].append(
        {
            "id": "existing:suggestion_card_conflict_marker",
            "event_type": "append_idempotency_marker",
            "at_ms": 3701,
            "sequence": 1000,
            "source": "live_asr_stream",
            "trace_kind": "live_event",
            "idempotency_key": (
                "live_asr_card_lifecycle_append:"
                "persisted_asr_append_repository_conflict_review:"
                "asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001:"
                "suggestion_card:card_dry_run_001"
            ),
            "payload": {},
        }
    )
    record_path.write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )

    second_client = TestClient(create_app(data_dir=tmp_path))
    events_before_response = second_client.get(
        "/live/asr/sessions/persisted_asr_append_repository_conflict_review/events"
    )
    response = second_client.post(
        (
            "/live/asr/sessions/persisted_asr_append_repository_conflict_review"
            "/llm-card-lifecycle-append-repository-dry-runs"
        ),
        json={
            "mode": "dry_run_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )
    events_after_response = second_client.get(
        "/live/asr/sessions/persisted_asr_append_repository_conflict_review/events"
    )

    assert create_response.status_code == 201
    assert events_before_response.status_code == 200
    assert response.status_code == 200
    body = response.json()
    assert body["repository_dry_run_status"] == "blocked_by_preflight"
    assert body["append_preflight_status"] == "blocked"
    assert body["safe_to_append_events"] is False
    assert body["append_errors"] == [
        {
            "field": "future_event_id",
            "code": "existing_event_id",
            "message": (
                "future event already exists: "
                "llm_schema_result:card_dry_run_001"
            ),
        },
        {
            "field": "idempotency_key",
            "code": "existing_idempotency_key",
            "message": (
                "future idempotency key already exists for event: "
                "suggestion_card:card_dry_run_001"
            ),
        },
    ]
    assert [result["repository_result_status"] for result in body["repository_results"]] == [
        "blocked_by_preflight",
        "blocked_by_preflight",
    ]
    assert [result["preflight_conflict_status"] for result in body["repository_results"]] == [
        "existing_event_id",
        "existing_idempotency_key",
    ]
    assert events_before_response.json()["events"] == events_after_response.json()["events"]
    for forbidden in forbidden_values:
        assert forbidden not in response.text


def test_asr_live_llm_card_lifecycle_append_repository_dry_run_endpoint_returns_404_for_unknown_request_id(
    monkeypatch,
    tmp_path,
):
    forbidden_values = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "append_repository_unknown_request",
    )
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(
            session_id="local_asr_append_repository_unknown_request_review"
        ),
    )

    response = client.post(
        (
            "/live/asr/sessions/local_asr_append_repository_unknown_request_review"
            "/llm-card-lifecycle-append-repository-dry-runs"
        ),
        json={
            "mode": "dry_run_only",
            "request_id": "missing_request_id",
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 404
    assert (
        "LLM request draft not found for card lifecycle preview dry-run: missing_request_id"
        in response.text
    )
    for forbidden in forbidden_values:
        assert forbidden not in response.text


def test_asr_live_llm_card_lifecycle_append_repository_dry_run_endpoint_reads_persisted_record_across_app_instances(
    tmp_path,
):
    first_client = TestClient(create_app(data_dir=tmp_path))
    create_response = first_client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(
            session_id="persisted_asr_append_repository_review"
        ),
    )

    second_client = TestClient(create_app(data_dir=tmp_path))
    record_path = tmp_path / "live_asr_sessions" / "persisted_asr_append_repository_review.json"
    record_before = record_path.read_bytes()
    response = second_client.post(
        (
            "/live/asr/sessions/persisted_asr_append_repository_review"
            "/llm-card-lifecycle-append-repository-dry-runs"
        ),
        json={
            "mode": "dry_run_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    assert record_path.read_bytes() == record_before
    body = response.json()
    assert body["session_id"] == "persisted_asr_append_repository_review"
    assert body["repository_dry_run_status"] == "would_append_if_enabled"
    assert body["repository_results"][0]["repository_idempotency_key"] == (
        "live_asr_card_lifecycle_repository_dry_run:"
        "persisted_asr_append_repository_review:"
        "asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001:"
        "llm_schema_result:card_dry_run_001"
    )


def test_asr_live_llm_card_lifecycle_append_repository_dry_run_endpoint_returns_404_for_missing_session(
    monkeypatch,
    tmp_path,
):
    forbidden_values = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "append_repository_missing_session",
    )
    client = TestClient(create_app())

    response = client.post(
        (
            "/live/asr/sessions/missing_asr_append_repository_review"
            "/llm-card-lifecycle-append-repository-dry-runs"
        ),
        json={
            "mode": "dry_run_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert response.status_code == 404
    assert "ASR live session not found: missing_asr_append_repository_review" in response.text
    for forbidden in forbidden_values:
        assert forbidden not in response.text


def test_asr_live_llm_card_lifecycle_append_repository_dry_run_endpoint_rejects_request_shape_errors(
    monkeypatch,
    tmp_path,
):
    forbidden_values = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "append_repository_shape",
    )
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(
            session_id="local_asr_append_repository_shape_review"
        ),
    )
    path = (
        "/live/asr/sessions/local_asr_append_repository_shape_review"
        "/llm-card-lifecycle-append-repository-dry-runs"
    )

    cases = [
        ([], "request body must be an object"),
        ({}, "missing mode"),
        (
            {
                "mode": 123,
                "request_id": "request",
                "candidate_response": {},
            },
            "mode must be a string",
        ),
        (
            {
                "mode": "dry_run_only",
                "request_id": 123,
                "candidate_response": {},
            },
            "request_id must be a string",
        ),
        (
            {
                "mode": "enabled",
                "request_id": "request",
                "candidate_response": {},
            },
            "unsupported card lifecycle append repository dry-run mode: enabled",
        ),
        (
            {
                "mode": " dry_run_only ",
                "request_id": "request",
                "candidate_response": {},
            },
            "unsupported card lifecycle append repository dry-run mode:  dry_run_only ",
        ),
        (
            {
                "mode": "dry_run_only",
                "candidate_response": {},
            },
            "missing request_id",
        ),
        (
            {
                "mode": "dry_run_only",
                "request_id": "request",
            },
            "missing candidate_response",
        ),
        (
            {
                "mode": "dry_run_only",
                "request_id": "request",
                "candidate_response": [],
            },
            "candidate_response must be an object",
        ),
        (
            {
                "mode": "dry_run_only",
                "request_id": "request",
                "candidate_response": {},
                "api_key": "ignored-test-value",
            },
            "extra fields are not permitted: api_key",
        ),
    ]
    assert create_response.status_code == 201
    for payload, expected_detail in cases:
        response = client.post(path, json=payload)
        assert response.status_code == 422
        assert expected_detail in response.text or expected_detail in response.json()["detail"]
        for forbidden in forbidden_values:
            assert forbidden not in response.text


def test_asr_live_llm_card_lifecycle_append_transaction_runs_disabled_endpoint_returns_transaction_contract_without_mutating_events(
    monkeypatch,
    tmp_path,
):
    forbidden_values = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "append_transaction_disabled",
    )
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(
            session_id="local_asr_append_transaction_disabled_review"
        ),
    )
    events_before_response = client.get(
        "/live/asr/sessions/local_asr_append_transaction_disabled_review/events"
    )

    response = client.post(
        (
            "/live/asr/sessions/local_asr_append_transaction_disabled_review"
            "/llm-card-lifecycle-append-transaction-runs"
        ),
        json={
            "mode": "disabled",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )
    events_after_response = client.get(
        "/live/asr/sessions/local_asr_append_transaction_disabled_review/events"
    )

    assert create_response.status_code == 201
    assert events_before_response.status_code == 200
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == "local_asr_append_transaction_disabled_review"
    assert body["source"] == "live_asr_stream"
    assert body["trace_kind"] == "live_event"
    assert body["transaction_run_mode"] == "disabled"
    assert body["transaction_run_status"] == "skipped"
    assert body["repository_dry_run_status"] == "would_append_if_enabled"
    assert body["append_run_status"] == "skipped"
    assert body["append_preflight_status"] == "allowed"
    assert body["future_lifecycle_status"] == "would_create_card"
    assert body["repository_transaction_status"] == "disabled"
    assert body["idempotency_store_write_status"] == "not_written"
    assert body["event_append_status"] == "not_appended"
    assert body["idempotency_store_status"] == "not_written"
    assert body["safe_to_commit_transaction"] is False
    assert body["safe_to_append_events"] is False
    assert body["safe_to_create_card"] is False
    assert body["append_errors"] == []
    assert body["append_plan_count"] == 2
    assert body["append_run_count"] == 2
    assert body["repository_append_count"] == 2
    assert body["transaction_run_count"] == 2
    assert body["would_append_event_types_if_enabled"] == [
        "llm_schema_result",
        "suggestion_card",
    ]
    runs = body["transaction_runs"]
    assert [run["event_type"] for run in runs] == [
        "llm_schema_result",
        "suggestion_card",
    ]
    assert [run["future_event_id"] for run in runs] == [
        "llm_schema_result:card_dry_run_001",
        "suggestion_card:card_dry_run_001",
    ]
    assert [run["transaction_run_id"] for run in runs] == [
        (
            "asr_card_lifecycle_append_transaction_run_disabled_"
            "llm_schema_result_card_dry_run_001"
        ),
        (
            "asr_card_lifecycle_append_transaction_run_disabled_"
            "suggestion_card_card_dry_run_001"
        ),
    ]
    assert [run["transaction_idempotency_key"] for run in runs] == [
        (
            "live_asr_card_lifecycle_append_transaction_run:disabled:"
            "local_asr_append_transaction_disabled_review:"
            "asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001:"
            "llm_schema_result:card_dry_run_001"
        ),
        (
            "live_asr_card_lifecycle_append_transaction_run:disabled:"
            "local_asr_append_transaction_disabled_review:"
            "asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001:"
            "suggestion_card:card_dry_run_001"
        ),
    ]
    assert [run["repository_result_id"] for run in runs] == [
        result["repository_result_id"] for result in body["repository_results"]
    ]
    assert [run["repository_idempotency_key"] for run in runs] == [
        result["repository_idempotency_key"] for result in body["repository_results"]
    ]
    assert {run["transaction_run_status"] for run in runs} == {"skipped"}
    assert {run["skip_reason"] for run in runs} == {
        "repository_transaction_disabled"
    }
    assert {run["repository_result_status"] for run in runs} == {
        "would_append_if_enabled"
    }
    assert {run["preflight_append_status"] for run in runs} == {
        "would_append_once_if_enabled"
    }
    assert {run["preflight_conflict_status"] for run in runs} == {"none"}
    assert {run["repository_write_status"] for run in runs} == {
        "dry_run_only"
    }
    assert {run["transaction_write_status"] for run in runs} == {"disabled"}
    assert {run["event_append_status"] for run in runs} == {"not_appended"}
    assert {run["idempotency_store_status"] for run in runs} == {"not_written"}
    assert {run["idempotency_store_write_status"] for run in runs} == {
        "not_written"
    }
    assert {run["safe_to_commit_transaction"] for run in runs} == {False}
    assert {run["safe_to_append_event"] for run in runs} == {False}
    assert body["block_reasons"] == [
        "repository_transaction_disabled",
        "idempotency_store_write_disabled",
        "event_mutation_disabled",
    ]
    assert "repository_transaction_commit_contract" in body["next_required_decisions"]
    assert events_after_response.status_code == 200
    assert events_before_response.json()["events"] == events_after_response.json()["events"]
    assert events_after_response.json()["events"] == create_response.json()["live_events"]
    for forbidden in forbidden_values:
        assert forbidden not in response.text
    assert "llm_schema_result" not in [
        event["event_type"] for event in events_after_response.json()["events"]
    ]
    assert "suggestion_card" not in [
        event["event_type"] for event in events_after_response.json()["events"]
    ]
    assert "suggestion_silenced" not in [
        event["event_type"] for event in events_after_response.json()["events"]
    ]


def test_asr_live_llm_card_lifecycle_append_transaction_runs_disabled_endpoint_uses_collision_resistant_transaction_identifiers():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(
            session_id="local_asr_append_transaction_identifier_review"
        ),
    )
    request_id = (
        "asr_llm_request_draft_"
        "asr_suggestion_candidate_asr_state_event_asr_seg_001"
    )
    colon_candidate = _valid_schema_validation_candidate_response()
    colon_candidate["id"] = "card:dry_run:001"
    underscore_candidate = _valid_schema_validation_candidate_response()
    underscore_candidate["id"] = "card_dry_run_001"

    colon_response = client.post(
        (
            "/live/asr/sessions/local_asr_append_transaction_identifier_review"
            "/llm-card-lifecycle-append-transaction-runs"
        ),
        json={
            "mode": "disabled",
            "request_id": request_id,
            "candidate_response": colon_candidate,
        },
    )
    underscore_response = client.post(
        (
            "/live/asr/sessions/local_asr_append_transaction_identifier_review"
            "/llm-card-lifecycle-append-transaction-runs"
        ),
        json={
            "mode": "disabled",
            "request_id": request_id,
            "candidate_response": underscore_candidate,
        },
    )

    assert create_response.status_code == 201
    assert colon_response.status_code == 200
    assert underscore_response.status_code == 200
    colon_runs = colon_response.json()["transaction_runs"]
    underscore_runs = underscore_response.json()["transaction_runs"]
    assert [run["transaction_run_id"] for run in colon_runs] != [
        run["transaction_run_id"] for run in underscore_runs
    ]
    assert [run["transaction_idempotency_key"] for run in colon_runs] != [
        run["transaction_idempotency_key"] for run in underscore_runs
    ]
    assert all(
        "card%3Adry_run%3A001" in run["transaction_run_id"]
        for run in colon_runs
    )
    assert all(
        "card%3Adry_run%3A001" in run["transaction_idempotency_key"]
        for run in colon_runs
    )


def test_asr_live_llm_card_lifecycle_append_transaction_runs_disabled_endpoint_skips_schema_invalid_silenced_runs(
    monkeypatch,
    tmp_path,
):
    forbidden_values = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "append_transaction_schema_invalid",
    )
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(
            session_id="local_asr_append_transaction_schema_invalid_review"
        ),
    )
    candidate = _valid_schema_validation_candidate_response()
    candidate["usage"] = {}
    candidate["schema_result"] = "failed"

    response = client.post(
        (
            "/live/asr/sessions/local_asr_append_transaction_schema_invalid_review"
            "/llm-card-lifecycle-append-transaction-runs"
        ),
        json={
            "mode": "disabled",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": candidate,
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["transaction_run_status"] == "skipped"
    assert body["repository_dry_run_status"] == "would_append_if_enabled"
    assert body["future_lifecycle_status"] == "would_silence_candidate"
    assert body["schema_validation_status"] == "dry_run_failed"
    assert body["card_creation_policy_status"] == "dry_run_blocked"
    assert [run["event_type"] for run in body["transaction_runs"]] == [
        "llm_schema_result",
        "suggestion_silenced",
    ]
    assert {run["skip_reason"] for run in body["transaction_runs"]} == {
        "repository_transaction_disabled"
    }
    assert {
        "field": "schema_validation",
        "code": "schema_validation_failed",
    } in [
        {"field": error["field"], "code": error["code"]}
        for error in body["policy_errors"]
    ]
    for forbidden in forbidden_values:
        assert forbidden not in response.text


def test_asr_live_llm_card_lifecycle_append_transaction_runs_disabled_endpoint_skips_policy_blocked_silenced_runs(
    monkeypatch,
    tmp_path,
):
    forbidden_values = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "append_transaction_policy_blocked",
    )
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(
            session_id="local_asr_append_transaction_policy_blocked_review"
        ),
    )

    response = client.post(
        (
            "/live/asr/sessions/local_asr_append_transaction_policy_blocked_review"
            "/llm-card-lifecycle-append-transaction-runs"
        ),
        json={
            "mode": "disabled",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["transaction_run_status"] == "skipped"
    assert body["future_lifecycle_status"] == "would_silence_candidate"
    assert body["schema_validation_status"] == "dry_run_passed"
    assert body["card_creation_policy_status"] == "dry_run_blocked"
    assert [run["event_type"] for run in body["transaction_runs"]] == [
        "llm_schema_result",
        "suggestion_silenced",
    ]
    assert {run["skip_reason"] for run in body["transaction_runs"]} == {
        "repository_transaction_disabled"
    }
    assert {
        "field": "evidence_span_ids",
        "code": "stale_evidence",
    } in [
        {"field": error["field"], "code": error["code"]}
        for error in body["policy_errors"]
    ]
    for forbidden in forbidden_values:
        assert forbidden not in response.text


def test_asr_live_llm_card_lifecycle_append_transaction_runs_disabled_endpoint_preserves_preflight_conflicts(
    monkeypatch,
    tmp_path,
):
    forbidden_values = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "append_transaction_conflict",
    )
    first_client = TestClient(create_app(data_dir=tmp_path))
    create_response = first_client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(
            session_id="persisted_asr_append_transaction_conflict_review"
        ),
    )
    record_path = (
        tmp_path
        / "live_asr_sessions"
        / "persisted_asr_append_transaction_conflict_review.json"
    )
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["events"].append(
        {
            "id": "llm_schema_result:card_dry_run_001",
            "event_type": "llm_schema_result",
            "at_ms": 3700,
            "sequence": 999,
            "source": "live_asr_stream",
            "trace_kind": "live_event",
            "payload": {
                "card_id": "card_dry_run_001",
                "idempotency_key": (
                    "live_asr_card_lifecycle_append:"
                    "persisted_asr_append_transaction_conflict_review:"
                    "asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001:"
                    "llm_schema_result:card_dry_run_001"
                ),
            },
        }
    )
    record["events"].append(
        {
            "id": "existing:suggestion_card_conflict_marker",
            "event_type": "append_idempotency_marker",
            "at_ms": 3701,
            "sequence": 1000,
            "source": "live_asr_stream",
            "trace_kind": "live_event",
            "idempotency_key": (
                "live_asr_card_lifecycle_append:"
                "persisted_asr_append_transaction_conflict_review:"
                "asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001:"
                "suggestion_card:card_dry_run_001"
            ),
            "payload": {},
        }
    )
    record_path.write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )

    second_client = TestClient(create_app(data_dir=tmp_path))
    events_before_response = second_client.get(
        "/live/asr/sessions/persisted_asr_append_transaction_conflict_review/events"
    )
    response = second_client.post(
        (
            "/live/asr/sessions/persisted_asr_append_transaction_conflict_review"
            "/llm-card-lifecycle-append-transaction-runs"
        ),
        json={
            "mode": "disabled",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )
    events_after_response = second_client.get(
        "/live/asr/sessions/persisted_asr_append_transaction_conflict_review/events"
    )

    assert create_response.status_code == 201
    assert events_before_response.status_code == 200
    assert response.status_code == 200
    body = response.json()
    assert body["transaction_run_status"] == "skipped"
    assert body["repository_dry_run_status"] == "blocked_by_preflight"
    assert body["append_preflight_status"] == "blocked"
    assert body["append_errors"] == [
        {
            "field": "future_event_id",
            "code": "existing_event_id",
            "message": (
                "future event already exists: "
                "llm_schema_result:card_dry_run_001"
            ),
        },
        {
            "field": "idempotency_key",
            "code": "existing_idempotency_key",
            "message": (
                "future idempotency key already exists for event: "
                "suggestion_card:card_dry_run_001"
            ),
        },
    ]
    assert [run["skip_reason"] for run in body["transaction_runs"]] == [
        "repository_preflight_blocked",
        "repository_preflight_blocked",
    ]
    assert [run["preflight_conflict_status"] for run in body["transaction_runs"]] == [
        "existing_event_id",
        "existing_idempotency_key",
    ]
    assert [run["repository_result_status"] for run in body["transaction_runs"]] == [
        "blocked_by_preflight",
        "blocked_by_preflight",
    ]
    assert events_before_response.json()["events"] == events_after_response.json()["events"]
    for forbidden in forbidden_values:
        assert forbidden not in response.text


def test_asr_live_llm_card_lifecycle_append_transaction_runs_disabled_endpoint_returns_404_for_unknown_request_id(
    monkeypatch,
    tmp_path,
):
    forbidden_values = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "append_transaction_unknown_request",
    )
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(
            session_id="local_asr_append_transaction_unknown_request_review"
        ),
    )

    response = client.post(
        (
            "/live/asr/sessions/local_asr_append_transaction_unknown_request_review"
            "/llm-card-lifecycle-append-transaction-runs"
        ),
        json={
            "mode": "disabled",
            "request_id": "missing_request_id",
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 404
    assert (
        "LLM request draft not found for card lifecycle preview dry-run: missing_request_id"
        in response.text
    )
    for forbidden in forbidden_values:
        assert forbidden not in response.text


def test_asr_live_llm_card_lifecycle_append_transaction_runs_disabled_endpoint_reads_persisted_record_across_app_instances(
    tmp_path,
):
    first_client = TestClient(create_app(data_dir=tmp_path))
    create_response = first_client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(
            session_id="persisted_asr_append_transaction_review"
        ),
    )

    second_client = TestClient(create_app(data_dir=tmp_path))
    record_path = tmp_path / "live_asr_sessions" / "persisted_asr_append_transaction_review.json"
    record_before = record_path.read_bytes()
    response = second_client.post(
        (
            "/live/asr/sessions/persisted_asr_append_transaction_review"
            "/llm-card-lifecycle-append-transaction-runs"
        ),
        json={
            "mode": "disabled",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    assert record_path.read_bytes() == record_before
    body = response.json()
    assert body["session_id"] == "persisted_asr_append_transaction_review"
    assert body["transaction_run_status"] == "skipped"
    assert body["transaction_runs"][0]["transaction_idempotency_key"] == (
        "live_asr_card_lifecycle_append_transaction_run:disabled:"
        "persisted_asr_append_transaction_review:"
        "asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001:"
        "llm_schema_result:card_dry_run_001"
    )


def test_asr_live_llm_card_lifecycle_append_transaction_runs_disabled_endpoint_returns_404_for_missing_session(
    monkeypatch,
    tmp_path,
):
    forbidden_values = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "append_transaction_missing_session",
    )
    client = TestClient(create_app())

    response = client.post(
        (
            "/live/asr/sessions/missing_asr_append_transaction_review"
            "/llm-card-lifecycle-append-transaction-runs"
        ),
        json={
            "mode": "disabled",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert response.status_code == 404
    assert "ASR live session not found: missing_asr_append_transaction_review" in response.text
    for forbidden in forbidden_values:
        assert forbidden not in response.text


def test_asr_live_llm_card_lifecycle_append_transaction_runs_disabled_endpoint_rejects_request_shape_errors(
    monkeypatch,
    tmp_path,
):
    forbidden_values = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "append_transaction_shape",
    )
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(
            session_id="local_asr_append_transaction_shape_review"
        ),
    )
    path = (
        "/live/asr/sessions/local_asr_append_transaction_shape_review"
        "/llm-card-lifecycle-append-transaction-runs"
    )

    cases = [
        ([], "request body must be an object"),
        ({}, "missing mode"),
        (
            {
                "mode": 123,
                "request_id": "request",
                "candidate_response": {},
            },
            "mode must be a string",
        ),
        (
            {
                "mode": "disabled",
                "request_id": 123,
                "candidate_response": {},
            },
            "request_id must be a string",
        ),
        (
            {
                "mode": "enabled",
                "request_id": "request",
                "candidate_response": {},
            },
            "unsupported card lifecycle append transaction run mode: enabled",
        ),
        (
            {
                "mode": " disabled ",
                "request_id": "request",
                "candidate_response": {},
            },
            "unsupported card lifecycle append transaction run mode:  disabled ",
        ),
        (
            {
                "mode": "disabled",
                "candidate_response": {},
            },
            "missing request_id",
        ),
        (
            {
                "mode": "disabled",
                "request_id": "request",
            },
            "missing candidate_response",
        ),
        (
            {
                "mode": "disabled",
                "request_id": "request",
                "candidate_response": [],
            },
            "candidate_response must be an object",
        ),
        (
            {
                "mode": "disabled",
                "request_id": "request",
                "candidate_response": {},
                "api_key": "ignored-test-value",
            },
            "extra fields are not permitted: api_key",
        ),
    ]
    assert create_response.status_code == 201
    for payload, expected_detail in cases:
        response = client.post(path, json=payload)
        assert response.status_code == 422
        assert expected_detail in response.text or expected_detail in response.json()["detail"]
        for forbidden in forbidden_values:
            assert forbidden not in response.text


def test_asr_live_llm_card_lifecycle_append_result_audit_previews_endpoint_returns_audit_preview_without_mutating_events(
    monkeypatch,
    tmp_path,
):
    forbidden_values = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "append_result_audit_preview",
    )
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(
            session_id="local_asr_append_result_audit_preview_review"
        ),
    )
    events_before_response = client.get(
        "/live/asr/sessions/local_asr_append_result_audit_preview_review/events"
    )

    response = client.post(
        (
            "/live/asr/sessions/local_asr_append_result_audit_preview_review"
            "/llm-card-lifecycle-append-result-audit-previews"
        ),
        json={
            "mode": "preview_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )
    events_after_response = client.get(
        "/live/asr/sessions/local_asr_append_result_audit_preview_review/events"
    )

    assert create_response.status_code == 201
    assert events_before_response.status_code == 200
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == "local_asr_append_result_audit_preview_review"
    assert body["source"] == "live_asr_stream"
    assert body["trace_kind"] == "live_event"
    assert body["append_result_audit_mode"] == "preview_only"
    assert body["append_result_audit_status"] == "previewed"
    assert body["append_result_audit_event_status"] == "preview_only"
    assert body["transaction_run_status"] == "skipped"
    assert body["repository_dry_run_status"] == "would_append_if_enabled"
    assert body["append_preflight_status"] == "allowed"
    assert body["future_lifecycle_status"] == "would_create_card"
    assert body["audit_event_append_status"] == "not_appended"
    assert body["event_append_status"] == "not_appended"
    assert body["idempotency_store_status"] == "not_written"
    assert body["idempotency_store_write_status"] == "not_written"
    assert body["safe_to_write_audit_events"] is False
    assert body["safe_to_append_events"] is False
    assert body["safe_to_create_card"] is False
    assert body["append_errors"] == []
    assert body["append_plan_count"] == 2
    assert body["append_run_count"] == 2
    assert body["repository_append_count"] == 2
    assert body["transaction_run_count"] == 2
    assert body["append_result_audit_event_count"] == 2
    assert body["would_append_event_types_if_enabled"] == [
        "llm_schema_result",
        "suggestion_card",
    ]
    audit_events = body["append_result_audit_events"]
    assert [event["event_type"] for event in audit_events] == [
        "llm_schema_result",
        "suggestion_card",
    ]
    assert [event["future_event_id"] for event in audit_events] == [
        "llm_schema_result:card_dry_run_001",
        "suggestion_card:card_dry_run_001",
    ]
    assert [event["audit_event_id"] for event in audit_events] == [
        (
            "asr_card_lifecycle_append_result_audit_preview_"
            "llm_schema_result_card_dry_run_001"
        ),
        (
            "asr_card_lifecycle_append_result_audit_preview_"
            "suggestion_card_card_dry_run_001"
        ),
    ]
    assert [event["audit_idempotency_key"] for event in audit_events] == [
        (
            "live_asr_card_lifecycle_append_result_audit_preview:"
            "local_asr_append_result_audit_preview_review:"
            "asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001:"
            "llm_schema_result:card_dry_run_001"
        ),
        (
            "live_asr_card_lifecycle_append_result_audit_preview:"
            "local_asr_append_result_audit_preview_review:"
            "asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001:"
            "suggestion_card:card_dry_run_001"
        ),
    ]
    assert [event["transaction_run_id"] for event in audit_events] == [
        run["transaction_run_id"] for run in body["transaction_runs"]
    ]
    assert [event["transaction_idempotency_key"] for event in audit_events] == [
        run["transaction_idempotency_key"] for run in body["transaction_runs"]
    ]
    assert [event["repository_result_id"] for event in audit_events] == [
        run["repository_result_id"] for run in body["transaction_runs"]
    ]
    assert {event["audit_event_type"] for event in audit_events} == {
        "card_lifecycle_append_result"
    }
    assert {event["audit_event_status"] for event in audit_events} == {
        "preview_only"
    }
    assert {event["audit_result_status"] for event in audit_events} == {
        "skipped_transaction_disabled"
    }
    assert {event["transaction_run_status"] for event in audit_events} == {
        "skipped"
    }
    assert {event["repository_result_status"] for event in audit_events} == {
        "would_append_if_enabled"
    }
    assert {event["preflight_append_status"] for event in audit_events} == {
        "would_append_once_if_enabled"
    }
    assert {event["preflight_conflict_status"] for event in audit_events} == {
        "none"
    }
    assert {event["repository_transaction_status"] for event in audit_events} == {
        "disabled"
    }
    assert {event["repository_write_status"] for event in audit_events} == {
        "dry_run_only"
    }
    assert {event["transaction_write_status"] for event in audit_events} == {
        "disabled"
    }
    assert {event["event_append_status"] for event in audit_events} == {
        "not_appended"
    }
    assert {event["audit_event_append_status"] for event in audit_events} == {
        "not_appended"
    }
    assert {event["idempotency_store_status"] for event in audit_events} == {
        "not_written"
    }
    assert {event["idempotency_store_write_status"] for event in audit_events} == {
        "not_written"
    }
    assert {event["safe_to_write_audit_event"] for event in audit_events} == {
        False
    }
    assert {event["safe_to_append_event"] for event in audit_events} == {False}
    assert body["block_reasons"] == [
        "append_result_audit_preview_only",
        "repository_transaction_disabled",
        "idempotency_store_write_disabled",
        "event_mutation_disabled",
    ]
    assert "append_result_audit_event_persistence_contract" in body[
        "next_required_decisions"
    ]
    assert events_after_response.status_code == 200
    assert events_before_response.json()["events"] == events_after_response.json()["events"]
    assert events_after_response.json()["events"] == create_response.json()["live_events"]
    for forbidden in forbidden_values:
        assert forbidden not in response.text
    assert "card_lifecycle_append_result" not in [
        event["event_type"] for event in events_after_response.json()["events"]
    ]
    assert "llm_schema_result" not in [
        event["event_type"] for event in events_after_response.json()["events"]
    ]
    assert "suggestion_card" not in [
        event["event_type"] for event in events_after_response.json()["events"]
    ]
    assert "suggestion_silenced" not in [
        event["event_type"] for event in events_after_response.json()["events"]
    ]


def test_asr_live_llm_card_lifecycle_append_result_audit_previews_endpoint_uses_collision_resistant_audit_identifiers():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(
            session_id="local_asr_append_result_audit_identifier_review"
        ),
    )
    request_id = (
        "asr_llm_request_draft_"
        "asr_suggestion_candidate_asr_state_event_asr_seg_001"
    )
    colon_candidate = _valid_schema_validation_candidate_response()
    colon_candidate["id"] = "card:dry_run:001"
    underscore_candidate = _valid_schema_validation_candidate_response()
    underscore_candidate["id"] = "card_dry_run_001"

    colon_response = client.post(
        (
            "/live/asr/sessions/local_asr_append_result_audit_identifier_review"
            "/llm-card-lifecycle-append-result-audit-previews"
        ),
        json={
            "mode": "preview_only",
            "request_id": request_id,
            "candidate_response": colon_candidate,
        },
    )
    underscore_response = client.post(
        (
            "/live/asr/sessions/local_asr_append_result_audit_identifier_review"
            "/llm-card-lifecycle-append-result-audit-previews"
        ),
        json={
            "mode": "preview_only",
            "request_id": request_id,
            "candidate_response": underscore_candidate,
        },
    )

    assert create_response.status_code == 201
    assert colon_response.status_code == 200
    assert underscore_response.status_code == 200
    colon_events = colon_response.json()["append_result_audit_events"]
    underscore_events = underscore_response.json()["append_result_audit_events"]
    assert [event["audit_event_id"] for event in colon_events] != [
        event["audit_event_id"] for event in underscore_events
    ]
    assert [event["audit_idempotency_key"] for event in colon_events] != [
        event["audit_idempotency_key"] for event in underscore_events
    ]
    assert all(
        "card%3Adry_run%3A001" in event["audit_event_id"]
        for event in colon_events
    )
    assert all(
        "card%3Adry_run%3A001" in event["audit_idempotency_key"]
        for event in colon_events
    )


def test_asr_live_llm_card_lifecycle_append_result_audit_previews_endpoint_handles_schema_invalid_silenced_previews(
    monkeypatch,
    tmp_path,
):
    forbidden_values = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "append_result_audit_schema_invalid",
    )
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(
            session_id="local_asr_append_result_audit_schema_invalid_review"
        ),
    )
    candidate = _valid_schema_validation_candidate_response()
    candidate["usage"] = {}
    candidate["schema_result"] = "failed"

    response = client.post(
        (
            "/live/asr/sessions/local_asr_append_result_audit_schema_invalid_review"
            "/llm-card-lifecycle-append-result-audit-previews"
        ),
        json={
            "mode": "preview_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": candidate,
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["append_result_audit_status"] == "previewed"
    assert body["future_lifecycle_status"] == "would_silence_candidate"
    assert body["schema_validation_status"] == "dry_run_failed"
    assert body["card_creation_policy_status"] == "dry_run_blocked"
    assert [event["event_type"] for event in body["append_result_audit_events"]] == [
        "llm_schema_result",
        "suggestion_silenced",
    ]
    assert {event["audit_result_status"] for event in body["append_result_audit_events"]} == {
        "skipped_transaction_disabled"
    }
    assert {
        "field": "schema_validation",
        "code": "schema_validation_failed",
    } in [
        {"field": error["field"], "code": error["code"]}
        for error in body["policy_errors"]
    ]
    for forbidden in forbidden_values:
        assert forbidden not in response.text


def test_asr_live_llm_card_lifecycle_append_result_audit_previews_endpoint_handles_policy_blocked_silenced_previews(
    monkeypatch,
    tmp_path,
):
    forbidden_values = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "append_result_audit_policy_blocked",
    )
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(
            session_id="local_asr_append_result_audit_policy_blocked_review"
        ),
    )

    response = client.post(
        (
            "/live/asr/sessions/local_asr_append_result_audit_policy_blocked_review"
            "/llm-card-lifecycle-append-result-audit-previews"
        ),
        json={
            "mode": "preview_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["append_result_audit_status"] == "previewed"
    assert body["future_lifecycle_status"] == "would_silence_candidate"
    assert body["schema_validation_status"] == "dry_run_passed"
    assert body["card_creation_policy_status"] == "dry_run_blocked"
    assert [event["event_type"] for event in body["append_result_audit_events"]] == [
        "llm_schema_result",
        "suggestion_silenced",
    ]
    assert {event["audit_result_status"] for event in body["append_result_audit_events"]} == {
        "skipped_transaction_disabled"
    }
    assert {
        "field": "evidence_span_ids",
        "code": "stale_evidence",
    } in [
        {"field": error["field"], "code": error["code"]}
        for error in body["policy_errors"]
    ]
    for forbidden in forbidden_values:
        assert forbidden not in response.text


def test_asr_live_llm_card_lifecycle_append_result_audit_previews_endpoint_preserves_preflight_conflicts(
    monkeypatch,
    tmp_path,
):
    forbidden_values = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "append_result_audit_conflict",
    )
    first_client = TestClient(create_app(data_dir=tmp_path))
    create_response = first_client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(
            session_id="persisted_asr_append_result_audit_conflict_review"
        ),
    )
    record_path = (
        tmp_path
        / "live_asr_sessions"
        / "persisted_asr_append_result_audit_conflict_review.json"
    )
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["events"].append(
        {
            "id": "llm_schema_result:card_dry_run_001",
            "event_type": "llm_schema_result",
            "at_ms": 3700,
            "sequence": 999,
            "source": "live_asr_stream",
            "trace_kind": "live_event",
            "payload": {
                "card_id": "card_dry_run_001",
                "idempotency_key": (
                    "live_asr_card_lifecycle_append:"
                    "persisted_asr_append_result_audit_conflict_review:"
                    "asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001:"
                    "llm_schema_result:card_dry_run_001"
                ),
            },
        }
    )
    record["events"].append(
        {
            "id": "existing:suggestion_card_conflict_marker",
            "event_type": "append_idempotency_marker",
            "at_ms": 3701,
            "sequence": 1000,
            "source": "live_asr_stream",
            "trace_kind": "live_event",
            "idempotency_key": (
                "live_asr_card_lifecycle_append:"
                "persisted_asr_append_result_audit_conflict_review:"
                "asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001:"
                "suggestion_card:card_dry_run_001"
            ),
            "payload": {},
        }
    )
    record_path.write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )

    second_client = TestClient(create_app(data_dir=tmp_path))
    events_before_response = second_client.get(
        "/live/asr/sessions/persisted_asr_append_result_audit_conflict_review/events"
    )
    response = second_client.post(
        (
            "/live/asr/sessions/persisted_asr_append_result_audit_conflict_review"
            "/llm-card-lifecycle-append-result-audit-previews"
        ),
        json={
            "mode": "preview_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )
    events_after_response = second_client.get(
        "/live/asr/sessions/persisted_asr_append_result_audit_conflict_review/events"
    )

    assert create_response.status_code == 201
    assert events_before_response.status_code == 200
    assert response.status_code == 200
    body = response.json()
    assert body["append_result_audit_status"] == "previewed"
    assert body["repository_dry_run_status"] == "blocked_by_preflight"
    assert body["append_preflight_status"] == "blocked"
    assert body["append_errors"] == [
        {
            "field": "future_event_id",
            "code": "existing_event_id",
            "message": (
                "future event already exists: "
                "llm_schema_result:card_dry_run_001"
            ),
        },
        {
            "field": "idempotency_key",
            "code": "existing_idempotency_key",
            "message": (
                "future idempotency key already exists for event: "
                "suggestion_card:card_dry_run_001"
            ),
        },
    ]
    assert [event["audit_result_status"] for event in body["append_result_audit_events"]] == [
        "blocked_by_preflight",
        "blocked_by_preflight",
    ]
    assert [event["preflight_conflict_status"] for event in body["append_result_audit_events"]] == [
        "existing_event_id",
        "existing_idempotency_key",
    ]
    assert [event["repository_result_status"] for event in body["append_result_audit_events"]] == [
        "blocked_by_preflight",
        "blocked_by_preflight",
    ]
    assert events_before_response.json()["events"] == events_after_response.json()["events"]
    for forbidden in forbidden_values:
        assert forbidden not in response.text


def test_asr_live_llm_card_lifecycle_append_result_audit_previews_endpoint_returns_404_for_unknown_request_id(
    monkeypatch,
    tmp_path,
):
    forbidden_values = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "append_result_audit_unknown_request",
    )
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(
            session_id="local_asr_append_result_audit_unknown_request_review"
        ),
    )

    response = client.post(
        (
            "/live/asr/sessions/local_asr_append_result_audit_unknown_request_review"
            "/llm-card-lifecycle-append-result-audit-previews"
        ),
        json={
            "mode": "preview_only",
            "request_id": "missing_request_id",
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 404
    assert (
        "LLM request draft not found for card lifecycle preview dry-run: missing_request_id"
        in response.text
    )
    for forbidden in forbidden_values:
        assert forbidden not in response.text


def test_asr_live_llm_card_lifecycle_append_result_audit_previews_endpoint_reads_persisted_record_across_app_instances(
    tmp_path,
):
    first_client = TestClient(create_app(data_dir=tmp_path))
    create_response = first_client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(
            session_id="persisted_asr_append_result_audit_review"
        ),
    )

    second_client = TestClient(create_app(data_dir=tmp_path))
    record_path = tmp_path / "live_asr_sessions" / "persisted_asr_append_result_audit_review.json"
    record_before = record_path.read_bytes()
    response = second_client.post(
        (
            "/live/asr/sessions/persisted_asr_append_result_audit_review"
            "/llm-card-lifecycle-append-result-audit-previews"
        ),
        json={
            "mode": "preview_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    assert record_path.read_bytes() == record_before
    body = response.json()
    assert body["session_id"] == "persisted_asr_append_result_audit_review"
    assert body["append_result_audit_status"] == "previewed"
    assert body["append_result_audit_events"][0]["audit_idempotency_key"] == (
        "live_asr_card_lifecycle_append_result_audit_preview:"
        "persisted_asr_append_result_audit_review:"
        "asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001:"
        "llm_schema_result:card_dry_run_001"
    )


def test_asr_live_llm_card_lifecycle_append_result_audit_previews_endpoint_returns_404_for_missing_session(
    monkeypatch,
    tmp_path,
):
    forbidden_values = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "append_result_audit_missing_session",
    )
    client = TestClient(create_app())

    response = client.post(
        (
            "/live/asr/sessions/missing_asr_append_result_audit_review"
            "/llm-card-lifecycle-append-result-audit-previews"
        ),
        json={
            "mode": "preview_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert response.status_code == 404
    assert "ASR live session not found: missing_asr_append_result_audit_review" in response.text
    for forbidden in forbidden_values:
        assert forbidden not in response.text


def test_asr_live_llm_card_lifecycle_append_result_audit_previews_endpoint_rejects_request_shape_errors(
    monkeypatch,
    tmp_path,
):
    forbidden_values = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "append_result_audit_shape",
    )
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(
            session_id="local_asr_append_result_audit_shape_review"
        ),
    )
    path = (
        "/live/asr/sessions/local_asr_append_result_audit_shape_review"
        "/llm-card-lifecycle-append-result-audit-previews"
    )

    cases = [
        ([], "request body must be an object"),
        ({}, "missing mode"),
        (
            {
                "mode": 123,
                "request_id": "request",
                "candidate_response": {},
            },
            "mode must be a string",
        ),
        (
            {
                "mode": "preview_only",
                "request_id": 123,
                "candidate_response": {},
            },
            "request_id must be a string",
        ),
        (
            {
                "mode": "enabled",
                "request_id": "request",
                "candidate_response": {},
            },
            "unsupported card lifecycle append result audit preview mode: enabled",
        ),
        (
            {
                "mode": " preview_only ",
                "request_id": "request",
                "candidate_response": {},
            },
            (
                "unsupported card lifecycle append result audit preview mode: "
                " preview_only "
            ),
        ),
        (
            {
                "mode": "preview_only",
                "candidate_response": {},
            },
            "missing request_id",
        ),
        (
            {
                "mode": "preview_only",
                "request_id": "request",
            },
            "missing candidate_response",
        ),
        (
            {
                "mode": "preview_only",
                "request_id": "request",
                "candidate_response": [],
            },
            "candidate_response must be an object",
        ),
        (
            {
                "mode": "preview_only",
                "request_id": "request",
                "candidate_response": {},
                "api_key": "ignored-test-value",
            },
            "extra fields are not permitted: api_key",
        ),
    ]
    assert create_response.status_code == 201
    for payload, expected_detail in cases:
        response = client.post(path, json=payload)
        assert response.status_code == 422
        assert expected_detail in response.text
        for forbidden in forbidden_values:
            assert forbidden not in response.text


def test_asr_live_llm_card_lifecycle_retry_replay_preflights_endpoint_returns_no_existing_append_without_mutating_events(
    monkeypatch,
    tmp_path,
):
    forbidden_values = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "retry_replay_no_existing",
    )
    client = TestClient(create_app())
    session_id = "local_asr_retry_replay_no_existing_review"
    request_id = (
        "asr_llm_request_draft_"
        "asr_suggestion_candidate_asr_state_event_asr_seg_001"
    )
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(session_id=session_id),
    )
    events_before_response = client.get(f"/live/asr/sessions/{session_id}/events")

    response = client.post(
        f"/live/asr/sessions/{session_id}/llm-card-lifecycle-retry-replay-preflights",
        json={
            "mode": "preflight_only",
            "request_id": request_id,
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )
    events_after_response = client.get(f"/live/asr/sessions/{session_id}/events")

    assert create_response.status_code == 201
    assert events_before_response.status_code == 200
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == session_id
    assert body["source"] == "live_asr_stream"
    assert body["trace_kind"] == "live_event"
    assert body["append_result_audit_status"] == "previewed"
    assert body["retry_replay_preflight_mode"] == "preflight_only"
    assert body["retry_replay_preflight_status"] == "analyzed"
    assert body["retry_replay_resolution_status"] == "no_existing_append"
    assert body["safe_to_replay_existing_events"] is False
    assert body["safe_to_mutate_events"] is False
    assert body["safe_to_append_events"] is False
    assert body["safe_to_create_card"] is False
    assert body["event_append_status"] == "not_appended"
    assert body["idempotency_store_status"] == "not_written"
    assert body["idempotency_store_write_status"] == "not_written"
    assert body["retry_replay_check_count"] == 2
    assert body["append_result_audit_event_count"] == 2
    checks = body["retry_replay_checks"]
    assert [check["event_type"] for check in checks] == [
        "llm_schema_result",
        "suggestion_card",
    ]
    assert [check["future_event_id"] for check in checks] == [
        "llm_schema_result:card_dry_run_001",
        "suggestion_card:card_dry_run_001",
    ]
    assert [check["idempotency_key"] for check in checks] == [
        _card_lifecycle_append_idempotency_key(session_id, "llm_schema_result"),
        _card_lifecycle_append_idempotency_key(session_id, "suggestion_card"),
    ]
    assert [check["retry_replay_check_status"] for check in checks] == [
        "no_existing_append",
        "no_existing_append",
    ]
    assert [check["resolution_status"] for check in checks] == [
        "no_existing_append",
        "no_existing_append",
    ]
    assert {check["existing_event_match_status"] for check in checks} == {
        "not_found"
    }
    assert {check["existing_idempotency_match_status"] for check in checks} == {
        "not_found"
    }
    assert {check["safe_to_replay_event"] for check in checks} == {False}
    assert {check["safe_to_append_event"] for check in checks} == {False}
    assert body["block_reasons"] == [
        "retry_replay_preflight_only",
        "repository_transaction_disabled",
        "idempotency_store_write_disabled",
        "event_mutation_disabled",
    ]
    assert events_after_response.status_code == 200
    assert events_before_response.json()["events"] == events_after_response.json()["events"]
    assert events_after_response.json()["events"] == create_response.json()["live_events"]
    for forbidden in forbidden_values:
        assert forbidden not in response.text


def test_asr_live_llm_card_lifecycle_retry_replay_preflights_endpoint_classifies_matching_events_as_safe_replay(
    monkeypatch,
    tmp_path,
):
    forbidden_values = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "retry_replay_safe_replay",
    )
    session_id = "persisted_asr_retry_replay_safe_review"
    first_client = TestClient(create_app(data_dir=tmp_path))
    create_response = first_client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(session_id=session_id),
    )
    record_path = tmp_path / "live_asr_sessions" / f"{session_id}.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    _append_persisted_lifecycle_event(
        record,
        session_id=session_id,
        event_type="llm_schema_result",
        sequence=999,
    )
    _append_persisted_lifecycle_event(
        record,
        session_id=session_id,
        event_type="suggestion_card",
        sequence=1000,
    )
    record_path.write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )

    second_client = TestClient(create_app(data_dir=tmp_path))
    record_before = record_path.read_bytes()
    response = second_client.post(
        f"/live/asr/sessions/{session_id}/llm-card-lifecycle-retry-replay-preflights",
        json={
            "mode": "preflight_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    assert record_path.read_bytes() == record_before
    body = response.json()
    assert body["retry_replay_resolution_status"] == "safe_to_replay"
    assert body["safe_to_replay_existing_events"] is True
    assert body["safe_to_mutate_events"] is False
    assert [check["resolution_status"] for check in body["retry_replay_checks"]] == [
        "safe_replay_same_event",
        "safe_replay_same_event",
    ]
    assert {
        check["existing_event_match_status"]
        for check in body["retry_replay_checks"]
    } == {"same_event_id"}
    assert {
        check["existing_idempotency_match_status"]
        for check in body["retry_replay_checks"]
    } == {"same_idempotency_key"}
    assert {check["safe_to_replay_event"] for check in body["retry_replay_checks"]} == {
        True
    }
    assert {check["safe_to_append_event"] for check in body["retry_replay_checks"]} == {
        False
    }
    assert [check["existing_event_id"] for check in body["retry_replay_checks"]] == [
        "llm_schema_result:card_dry_run_001",
        "suggestion_card:card_dry_run_001",
    ]
    for forbidden in forbidden_values:
        assert forbidden not in response.text


def test_asr_live_llm_card_lifecycle_retry_replay_preflights_endpoint_blocks_mismatched_existing_event_id(
    monkeypatch,
    tmp_path,
):
    forbidden_values = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "retry_replay_mismatched_event",
    )
    session_id = "persisted_asr_retry_replay_mismatch_review"
    first_client = TestClient(create_app(data_dir=tmp_path))
    create_response = first_client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(session_id=session_id),
    )
    record_path = tmp_path / "live_asr_sessions" / f"{session_id}.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    _append_persisted_lifecycle_event(
        record,
        session_id=session_id,
        event_type="llm_schema_result",
        sequence=999,
        idempotency_key=(
            "live_asr_card_lifecycle_append:"
            f"{session_id}:different_request:llm_schema_result:card_dry_run_001"
        ),
    )
    record_path.write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )

    second_client = TestClient(create_app(data_dir=tmp_path))
    response = second_client.post(
        f"/live/asr/sessions/{session_id}/llm-card-lifecycle-retry-replay-preflights",
        json={
            "mode": "preflight_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["retry_replay_resolution_status"] == "blocked_by_conflict"
    assert [check["resolution_status"] for check in body["retry_replay_checks"]] == [
        "blocked_mismatched_replay",
        "no_existing_append",
    ]
    assert body["retry_replay_checks"][0]["existing_event_match_status"] == (
        "mismatched_event"
    )
    assert body["retry_replay_checks"][0]["existing_idempotency_match_status"] == (
        "mismatched_idempotency_key"
    )
    assert body["retry_replay_checks"][0]["existing_event_id"] == (
        "llm_schema_result:card_dry_run_001"
    )
    assert body["safe_to_replay_existing_events"] is False
    assert body["safe_to_mutate_events"] is False
    for forbidden in forbidden_values:
        assert forbidden not in response.text


def test_asr_live_llm_card_lifecycle_retry_replay_preflights_endpoint_blocks_same_key_event_with_mismatched_metadata(
    monkeypatch,
    tmp_path,
):
    forbidden_values = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "retry_replay_metadata_mismatch",
    )
    session_id = "persisted_asr_retry_replay_metadata_mismatch_review"
    first_client = TestClient(create_app(data_dir=tmp_path))
    create_response = first_client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(session_id=session_id),
    )
    record_path = tmp_path / "live_asr_sessions" / f"{session_id}.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    _append_persisted_lifecycle_event(
        record,
        session_id=session_id,
        event_type="llm_schema_result",
        sequence=999,
        payload_extra={
            "request_id": "different_request",
            "request_draft_event_id": "different_draft",
        },
    )
    _append_persisted_lifecycle_event(
        record,
        session_id=session_id,
        event_type="suggestion_card",
        sequence=1000,
        payload_extra={
            "card": {"id": "different_nested_card_id"},
        },
    )
    record_path.write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )

    second_client = TestClient(create_app(data_dir=tmp_path))
    response = second_client.post(
        f"/live/asr/sessions/{session_id}/llm-card-lifecycle-retry-replay-preflights",
        json={
            "mode": "preflight_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["retry_replay_resolution_status"] == "blocked_by_conflict"
    assert [check["resolution_status"] for check in body["retry_replay_checks"]] == [
        "blocked_mismatched_replay",
        "blocked_mismatched_replay",
    ]
    assert [check["existing_event_match_status"] for check in body["retry_replay_checks"]] == [
        "mismatched_event",
        "mismatched_event",
    ]
    assert body["safe_to_replay_existing_events"] is False
    assert body["safe_to_mutate_events"] is False
    for forbidden in forbidden_values:
        assert forbidden not in response.text


def test_asr_live_llm_card_lifecycle_retry_replay_preflights_endpoint_blocks_event_with_conflicting_internal_idempotency_keys(
    monkeypatch,
    tmp_path,
):
    forbidden_values = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "retry_replay_internal_key_mismatch",
    )
    session_id = "persisted_asr_retry_replay_internal_key_mismatch_review"
    first_client = TestClient(create_app(data_dir=tmp_path))
    create_response = first_client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(session_id=session_id),
    )
    record_path = tmp_path / "live_asr_sessions" / f"{session_id}.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    expected_key = _card_lifecycle_append_idempotency_key(
        session_id,
        "llm_schema_result",
    )
    _append_persisted_lifecycle_event(
        record,
        session_id=session_id,
        event_type="llm_schema_result",
        sequence=999,
        payload_extra={
            "idempotency_key": (
                "live_asr_card_lifecycle_append:"
                f"{session_id}:different_request:"
                "llm_schema_result:card_dry_run_001"
            )
        },
    )
    record["events"][-1]["idempotency_key"] = expected_key
    _append_persisted_lifecycle_event(
        record,
        session_id=session_id,
        event_type="suggestion_card",
        sequence=1000,
    )
    record_path.write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )

    second_client = TestClient(create_app(data_dir=tmp_path))
    response = second_client.post(
        f"/live/asr/sessions/{session_id}/llm-card-lifecycle-retry-replay-preflights",
        json={
            "mode": "preflight_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["retry_replay_resolution_status"] == "blocked_by_conflict"
    assert [check["resolution_status"] for check in body["retry_replay_checks"]] == [
        "blocked_mismatched_replay",
        "safe_replay_same_event",
    ]
    assert body["retry_replay_checks"][0]["existing_event_match_status"] == (
        "mismatched_event"
    )
    assert body["retry_replay_checks"][0]["existing_idempotency_match_status"] == (
        "mismatched_idempotency_key"
    )
    assert body["retry_replay_checks"][0]["safe_to_replay_event"] is False
    assert body["safe_to_replay_existing_events"] is False
    for forbidden in forbidden_values:
        assert forbidden not in response.text


def test_asr_live_llm_card_lifecycle_retry_replay_preflights_endpoint_blocks_duplicate_idempotency_evidence_even_with_matching_event(
    monkeypatch,
    tmp_path,
):
    forbidden_values = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "retry_replay_duplicate_idempotency",
    )
    session_id = "persisted_asr_retry_replay_duplicate_key_review"
    first_client = TestClient(create_app(data_dir=tmp_path))
    create_response = first_client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(session_id=session_id),
    )
    record_path = tmp_path / "live_asr_sessions" / f"{session_id}.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    _append_persisted_lifecycle_event(
        record,
        session_id=session_id,
        event_type="llm_schema_result",
        sequence=999,
    )
    record["events"].append(
        {
            "id": "existing:duplicate_schema_result_idempotency_marker",
            "event_type": "append_idempotency_marker",
            "at_ms": 4701,
            "sequence": 1000,
            "source": "live_asr_stream",
            "trace_kind": "live_event",
            "idempotency_key": _card_lifecycle_append_idempotency_key(
                session_id,
                "llm_schema_result",
            ),
            "payload": {},
        }
    )
    _append_persisted_lifecycle_event(
        record,
        session_id=session_id,
        event_type="suggestion_card",
        sequence=1001,
    )
    record_path.write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )

    second_client = TestClient(create_app(data_dir=tmp_path))
    response = second_client.post(
        f"/live/asr/sessions/{session_id}/llm-card-lifecycle-retry-replay-preflights",
        json={
            "mode": "preflight_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["retry_replay_resolution_status"] == "blocked_by_conflict"
    assert [check["resolution_status"] for check in body["retry_replay_checks"]] == [
        "blocked_existing_idempotency_key",
        "safe_replay_same_event",
    ]
    assert body["retry_replay_checks"][0]["existing_idempotency_match_status"] == (
        "duplicate_idempotency_key"
    )
    assert body["retry_replay_checks"][0]["safe_to_replay_event"] is False
    assert body["safe_to_replay_existing_events"] is False
    for forbidden in forbidden_values:
        assert forbidden not in response.text


def test_asr_live_llm_card_lifecycle_retry_replay_preflights_endpoint_blocks_existing_idempotency_marker_without_matching_event(
    monkeypatch,
    tmp_path,
):
    forbidden_values = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "retry_replay_idempotency_marker",
    )
    session_id = "persisted_asr_retry_replay_marker_review"
    first_client = TestClient(create_app(data_dir=tmp_path))
    create_response = first_client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(session_id=session_id),
    )
    record_path = tmp_path / "live_asr_sessions" / f"{session_id}.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["events"].append(
        {
            "id": "existing:suggestion_card_retry_replay_marker",
            "event_type": "append_idempotency_marker",
            "at_ms": 3701,
            "sequence": 1000,
            "source": "live_asr_stream",
            "trace_kind": "live_event",
            "idempotency_key": _card_lifecycle_append_idempotency_key(
                session_id,
                "suggestion_card",
            ),
            "payload": {},
        }
    )
    record_path.write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )

    second_client = TestClient(create_app(data_dir=tmp_path))
    response = second_client.post(
        f"/live/asr/sessions/{session_id}/llm-card-lifecycle-retry-replay-preflights",
        json={
            "mode": "preflight_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["retry_replay_resolution_status"] == "blocked_by_conflict"
    assert [check["resolution_status"] for check in body["retry_replay_checks"]] == [
        "no_existing_append",
        "blocked_existing_idempotency_key",
    ]
    marker_check = body["retry_replay_checks"][1]
    assert marker_check["existing_event_match_status"] == "not_found"
    assert marker_check["existing_idempotency_match_status"] == (
        "same_idempotency_key_different_event"
    )
    assert marker_check["existing_event_id"] == (
        "existing:suggestion_card_retry_replay_marker"
    )
    assert marker_check["existing_idempotency_key"] == (
        _card_lifecycle_append_idempotency_key(session_id, "suggestion_card")
    )
    assert marker_check["safe_to_replay_event"] is False
    for forbidden in forbidden_values:
        assert forbidden not in response.text


def test_asr_live_llm_card_lifecycle_retry_replay_preflights_endpoint_blocks_partial_replay(
    monkeypatch,
    tmp_path,
):
    forbidden_values = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "retry_replay_partial",
    )
    session_id = "persisted_asr_retry_replay_partial_review"
    first_client = TestClient(create_app(data_dir=tmp_path))
    create_response = first_client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(session_id=session_id),
    )
    record_path = tmp_path / "live_asr_sessions" / f"{session_id}.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    _append_persisted_lifecycle_event(
        record,
        session_id=session_id,
        event_type="llm_schema_result",
        sequence=999,
    )
    record_path.write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )

    second_client = TestClient(create_app(data_dir=tmp_path))
    response = second_client.post(
        f"/live/asr/sessions/{session_id}/llm-card-lifecycle-retry-replay-preflights",
        json={
            "mode": "preflight_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["retry_replay_resolution_status"] == "blocked_by_partial_replay"
    assert [check["resolution_status"] for check in body["retry_replay_checks"]] == [
        "safe_replay_same_event",
        "blocked_partial_replay",
    ]
    assert [check["safe_to_replay_event"] for check in body["retry_replay_checks"]] == [
        True,
        False,
    ]
    assert body["safe_to_replay_existing_events"] is False
    assert body["safe_to_mutate_events"] is False
    for forbidden in forbidden_values:
        assert forbidden not in response.text


def test_asr_live_llm_card_lifecycle_retry_replay_preflights_endpoint_returns_404_for_unknown_request_id(
    monkeypatch,
    tmp_path,
):
    forbidden_values = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "retry_replay_unknown_request",
    )
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(
            session_id="local_asr_retry_replay_unknown_request_review"
        ),
    )

    response = client.post(
        (
            "/live/asr/sessions/local_asr_retry_replay_unknown_request_review"
            "/llm-card-lifecycle-retry-replay-preflights"
        ),
        json={
            "mode": "preflight_only",
            "request_id": "missing_request_id",
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 404
    assert (
        "LLM request draft not found for card lifecycle preview dry-run: missing_request_id"
        in response.text
    )
    for forbidden in forbidden_values:
        assert forbidden not in response.text


def test_asr_live_llm_card_lifecycle_retry_replay_preflights_endpoint_reads_persisted_record_across_app_instances(
    tmp_path,
):
    session_id = "persisted_asr_retry_replay_review"
    first_client = TestClient(create_app(data_dir=tmp_path))
    create_response = first_client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(session_id=session_id),
    )

    second_client = TestClient(create_app(data_dir=tmp_path))
    record_path = tmp_path / "live_asr_sessions" / f"{session_id}.json"
    record_before = record_path.read_bytes()
    response = second_client.post(
        f"/live/asr/sessions/{session_id}/llm-card-lifecycle-retry-replay-preflights",
        json={
            "mode": "preflight_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    assert record_path.read_bytes() == record_before
    body = response.json()
    assert body["session_id"] == session_id
    assert body["retry_replay_resolution_status"] == "no_existing_append"
    assert body["retry_replay_checks"][0]["idempotency_key"] == (
        _card_lifecycle_append_idempotency_key(session_id, "llm_schema_result")
    )


def test_asr_live_llm_card_lifecycle_retry_replay_preflights_endpoint_returns_404_for_missing_session(
    monkeypatch,
    tmp_path,
):
    forbidden_values = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "retry_replay_missing_session",
    )
    client = TestClient(create_app())

    response = client.post(
        (
            "/live/asr/sessions/missing_asr_retry_replay_review"
            "/llm-card-lifecycle-retry-replay-preflights"
        ),
        json={
            "mode": "preflight_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert response.status_code == 404
    assert "ASR live session not found: missing_asr_retry_replay_review" in response.text
    for forbidden in forbidden_values:
        assert forbidden not in response.text


def test_asr_live_llm_card_lifecycle_retry_replay_preflights_endpoint_rejects_request_shape_errors(
    monkeypatch,
    tmp_path,
):
    forbidden_values = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "retry_replay_shape",
    )
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(
            session_id="local_asr_retry_replay_shape_review"
        ),
    )
    path = (
        "/live/asr/sessions/local_asr_retry_replay_shape_review"
        "/llm-card-lifecycle-retry-replay-preflights"
    )

    cases = [
        ([], "request body must be an object"),
        ({}, "missing mode"),
        (
            {
                "mode": 123,
                "request_id": "request",
                "candidate_response": {},
            },
            "mode must be a string",
        ),
        (
            {
                "mode": "preflight_only",
                "request_id": 123,
                "candidate_response": {},
            },
            "request_id must be a string",
        ),
        (
            {
                "mode": "enabled",
                "request_id": "request",
                "candidate_response": {},
            },
            "unsupported card lifecycle retry replay preflight mode: enabled",
        ),
        (
            {
                "mode": " preflight_only ",
                "request_id": "request",
                "candidate_response": {},
            },
            (
                "unsupported card lifecycle retry replay preflight mode: "
                " preflight_only "
            ),
        ),
        (
            {
                "mode": "preflight_only",
                "candidate_response": {},
            },
            "missing request_id",
        ),
        (
            {
                "mode": "preflight_only",
                "request_id": "request",
            },
            "missing candidate_response",
        ),
        (
            {
                "mode": "preflight_only",
                "request_id": "request",
                "candidate_response": [],
            },
            "candidate_response must be an object",
        ),
        (
            {
                "mode": "preflight_only",
                "request_id": "request",
                "candidate_response": {},
                "api_key": "ignored-test-value",
            },
            "extra fields are not permitted: api_key",
        ),
    ]
    assert create_response.status_code == 201
    for payload, expected_detail in cases:
        response = client.post(path, json=payload)
        assert response.status_code == 422
        assert expected_detail in response.text
        for forbidden in forbidden_values:
            assert forbidden not in response.text


def test_asr_live_llm_card_lifecycle_append_event_serializer_dry_run_endpoint_serializes_allowed_events_without_mutating_events(
    monkeypatch,
    tmp_path,
):
    forbidden_values = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "append_event_serializer_allowed",
    )
    client = TestClient(create_app())
    session_id = "local_asr_append_event_serializer_allowed_review"
    request_id = (
        "asr_llm_request_draft_"
        "asr_suggestion_candidate_asr_state_event_asr_seg_001"
    )
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(session_id=session_id),
    )
    events_before_response = client.get(f"/live/asr/sessions/{session_id}/events")

    response = client.post(
        (
            f"/live/asr/sessions/{session_id}"
            "/llm-card-lifecycle-append-event-serializer-dry-runs"
        ),
        json={
            "mode": "dry_run_only",
            "request_id": request_id,
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )
    events_after_response = client.get(f"/live/asr/sessions/{session_id}/events")

    assert create_response.status_code == 201
    assert events_before_response.status_code == 200
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == session_id
    assert body["source"] == "live_asr_stream"
    assert body["trace_kind"] == "live_event"
    assert body["append_preflight_status"] == "allowed"
    assert body["append_event_serializer_mode"] == "dry_run_only"
    assert body["append_event_serializer_status"] == "serialized"
    assert body["append_event_serialization_status"] == "would_serialize_if_enabled"
    assert body["append_event_count"] == 2
    assert body["event_append_status"] == "not_appended"
    assert body["idempotency_store_status"] == "not_written"
    assert body["idempotency_store_write_status"] == "not_written"
    assert body["safe_to_append_events"] is False
    assert body["safe_to_create_card"] is False
    serialized_events = body["serialized_append_events"]
    append_plan = body["append_plan"]
    assert [event["event_type"] for event in serialized_events] == [
        "llm_schema_result",
        "suggestion_card",
    ]
    assert [event["event_id"] for event in serialized_events] == [
        "llm_schema_result:card_dry_run_001",
        "suggestion_card:card_dry_run_001",
    ]
    assert [event["id"] for event in serialized_events] == [
        "llm_schema_result:card_dry_run_001",
        "suggestion_card:card_dry_run_001",
    ]
    assert [event["sequence"] for event in serialized_events] == [
        append_plan[0]["would_append_sequence"],
        append_plan[1]["would_append_sequence"],
    ]
    assert [event["at_ms"] for event in serialized_events] == [3700, 3700]
    preview_events_by_id = {
        event["event_id"]: event for event in body["preview_events"]
    }
    append_plan_by_future_id = {
        item["future_event_id"]: item for item in append_plan
    }
    for serialized_event in serialized_events:
        assert serialized_event["preview_event_id"] in preview_events_by_id
        preview_event = preview_events_by_id[serialized_event["preview_event_id"]]
        append_plan_item = append_plan_by_future_id[serialized_event["future_event_id"]]
        assert serialized_event["id"] == serialized_event["event_id"]
        assert serialized_event["future_event_id"] == serialized_event["event_id"]
        assert serialized_event["event_id"] == append_plan_item["future_event_id"]
        assert serialized_event["event_type"] == preview_event["event_type"]
        assert serialized_event["at_ms"] == preview_event["at_ms"]
        assert serialized_event["at_ms"] == append_plan_item["at_ms"]
        expected_payload = dict(preview_event["payload"])
        expected_payload["idempotency_key"] = serialized_event["idempotency_key"]
        assert serialized_event["payload"] == expected_payload
    assert {event["source"] for event in serialized_events} == {"live_asr_stream"}
    assert {event["trace_kind"] for event in serialized_events} == {"live_event"}
    assert {event["serialization_status"] for event in serialized_events} == {
        "would_serialize_if_enabled"
    }
    assert {event["event_append_status"] for event in serialized_events} == {
        "not_appended"
    }
    assert {event["idempotency_store_status"] for event in serialized_events} == {
        "not_written"
    }
    assert {event["idempotency_store_write_status"] for event in serialized_events} == {
        "not_written"
    }
    assert {event["safe_to_append_event"] for event in serialized_events} == {False}
    assert {event["safe_to_create_card"] for event in serialized_events} == {False}
    assert [event["idempotency_key"] for event in serialized_events] == [
        _card_lifecycle_append_idempotency_key(session_id, "llm_schema_result"),
        _card_lifecycle_append_idempotency_key(session_id, "suggestion_card"),
    ]
    assert [
        event["payload"]["idempotency_key"] for event in serialized_events
    ] == [
        _card_lifecycle_append_idempotency_key(session_id, "llm_schema_result"),
        _card_lifecycle_append_idempotency_key(session_id, "suggestion_card"),
    ]
    assert {event["payload"]["request_id"] for event in serialized_events} == {
        request_id
    }
    assert {
        event["payload"]["request_draft_event_id"] for event in serialized_events
    } == {"llm_request_draft:asr_state_event_asr_seg_001"}
    assert {event["payload"]["card_id"] for event in serialized_events} == {
        "card_dry_run_001"
    }
    assert serialized_events[0]["payload"]["schema_result"] == "valid"
    assert serialized_events[0]["payload"]["show_or_silence_decision"] == "show"
    assert serialized_events[0]["payload"]["usage"] == {"total_tokens": 0}
    assert serialized_events[0]["payload"]["validation_errors"] == []
    assert serialized_events[1]["payload"]["card"]["id"] == "card_dry_run_001"
    assert serialized_events[1]["payload"]["card"]["title"] == "确认回滚负责人"
    assert serialized_events[1]["payload"]["card"]["status"] == "new"
    assert [event["append_status"] for event in serialized_events] == [
        "would_append_once_if_enabled",
        "would_append_once_if_enabled",
    ]
    assert [event["conflict_status"] for event in serialized_events] == [
        "none",
        "none",
    ]
    assert events_after_response.status_code == 200
    assert events_before_response.json()["events"] == events_after_response.json()["events"]
    assert events_after_response.json()["events"] == create_response.json()["live_events"]
    for forbidden in forbidden_values:
        assert forbidden not in response.text
    assert "llm_schema_result" not in [
        event["event_type"] for event in events_after_response.json()["events"]
    ]
    assert "suggestion_card" not in [
        event["event_type"] for event in events_after_response.json()["events"]
    ]


def test_asr_live_llm_card_lifecycle_append_event_serializer_dry_run_endpoint_serializes_schema_invalid_silenced_event():
    client = TestClient(create_app())
    session_id = "local_asr_append_event_serializer_schema_invalid_review"
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(session_id=session_id),
    )
    candidate = _valid_schema_validation_candidate_response()
    candidate["usage"] = {}
    candidate["schema_result"] = "failed"

    response = client.post(
        (
            f"/live/asr/sessions/{session_id}"
            "/llm-card-lifecycle-append-event-serializer-dry-runs"
        ),
        json={
            "mode": "dry_run_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": candidate,
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["append_event_serialization_status"] == "would_serialize_if_enabled"
    assert [event["event_type"] for event in body["serialized_append_events"]] == [
        "llm_schema_result",
        "suggestion_silenced",
    ]
    silenced_event = body["serialized_append_events"][1]
    assert silenced_event["event_id"] == "suggestion_silenced:card_dry_run_001"
    assert silenced_event["payload"]["silence_reason"] == "schema_validation_failed"
    assert silenced_event["payload"]["validation_errors"]
    assert silenced_event["event_append_status"] == "not_appended"


def test_asr_live_llm_card_lifecycle_append_event_serializer_dry_run_endpoint_serializes_policy_blocked_silenced_event():
    client = TestClient(create_app())
    session_id = "local_asr_append_event_serializer_policy_blocked_review"
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id=session_id),
    )

    response = client.post(
        (
            f"/live/asr/sessions/{session_id}"
            "/llm-card-lifecycle-append-event-serializer-dry-runs"
        ),
        json={
            "mode": "dry_run_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["append_event_serialization_status"] == "would_serialize_if_enabled"
    assert [event["event_type"] for event in body["serialized_append_events"]] == [
        "llm_schema_result",
        "suggestion_silenced",
    ]
    silenced_event = body["serialized_append_events"][1]
    assert silenced_event["payload"]["silence_reason"] == (
        "card_creation_policy_blocked"
    )
    assert {
        "field": "evidence_span_ids",
        "code": "stale_evidence",
    } in [
        {"field": error["field"], "code": error["code"]}
        for error in silenced_event["payload"]["policy_errors"]
    ]
    assert silenced_event["safe_to_append_event"] is False


def test_asr_live_llm_card_lifecycle_append_event_serializer_dry_run_endpoint_preserves_preflight_conflicts(
    tmp_path,
):
    session_id = "persisted_asr_append_event_serializer_conflict_review"
    first_client = TestClient(create_app(data_dir=tmp_path))
    create_response = first_client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(session_id=session_id),
    )
    record_path = tmp_path / "live_asr_sessions" / f"{session_id}.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    _append_persisted_lifecycle_event(
        record,
        session_id=session_id,
        event_type="llm_schema_result",
        sequence=999,
    )
    record_path.write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )

    second_client = TestClient(create_app(data_dir=tmp_path))
    record_before = record_path.read_bytes()
    response = second_client.post(
        (
            f"/live/asr/sessions/{session_id}"
            "/llm-card-lifecycle-append-event-serializer-dry-runs"
        ),
        json={
            "mode": "dry_run_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    assert record_path.read_bytes() == record_before
    body = response.json()
    assert body["append_preflight_status"] == "blocked"
    assert body["append_event_serialization_status"] == "blocked_by_preflight"
    assert body["append_errors"] == [
        {
            "field": "future_event_id",
            "code": "existing_event_id",
            "message": (
                "future event already exists: "
                "llm_schema_result:card_dry_run_001"
            ),
        }
    ]
    assert [event["serialization_status"] for event in body["serialized_append_events"]] == [
        "blocked_by_preflight",
        "would_serialize_if_enabled",
    ]
    assert [event["append_status"] for event in body["serialized_append_events"]] == [
        "blocked_existing_event",
        "would_append_once_if_enabled",
    ]
    assert body["event_append_status"] == "not_appended"
    assert body["idempotency_store_status"] == "not_written"
    assert body["safe_to_append_events"] is False


def test_asr_live_llm_card_lifecycle_append_event_serializer_dry_run_endpoint_preserves_payload_idempotency_preflight_conflicts(
    tmp_path,
):
    session_id = "persisted_asr_append_event_serializer_payload_key_review"
    first_client = TestClient(create_app(data_dir=tmp_path))
    create_response = first_client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(session_id=session_id),
    )
    record_path = tmp_path / "live_asr_sessions" / f"{session_id}.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["events"].append(
        {
            "id": "existing:append_event_serializer_payload_key_marker",
            "event_type": "append_idempotency_marker",
            "at_ms": 3700,
            "sequence": 999,
            "source": "live_asr_stream",
            "trace_kind": "live_event",
            "payload": {
                "idempotency_key": _card_lifecycle_append_idempotency_key(
                    session_id,
                    "suggestion_card",
                )
            },
        }
    )
    record_path.write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )

    second_client = TestClient(create_app(data_dir=tmp_path))
    record_before = record_path.read_bytes()
    response = second_client.post(
        (
            f"/live/asr/sessions/{session_id}"
            "/llm-card-lifecycle-append-event-serializer-dry-runs"
        ),
        json={
            "mode": "dry_run_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    assert record_path.read_bytes() == record_before
    body = response.json()
    assert body["append_preflight_status"] == "blocked"
    assert body["append_event_serialization_status"] == "blocked_by_preflight"
    assert body["append_errors"] == [
        {
            "field": "idempotency_key",
            "code": "existing_idempotency_key",
            "message": (
                "future idempotency key already exists for event: "
                "suggestion_card:card_dry_run_001"
            ),
        }
    ]
    assert [event["serialization_status"] for event in body["serialized_append_events"]] == [
        "would_serialize_if_enabled",
        "blocked_by_preflight",
    ]
    assert [event["append_status"] for event in body["serialized_append_events"]] == [
        "would_append_once_if_enabled",
        "blocked_existing_idempotency_key",
    ]
    assert [event["conflict_status"] for event in body["serialized_append_events"]] == [
        "none",
        "existing_idempotency_key",
    ]
    assert body["event_append_status"] == "not_appended"
    assert body["idempotency_store_status"] == "not_written"
    assert body["safe_to_append_events"] is False


def test_asr_live_llm_card_lifecycle_append_event_serializer_dry_run_endpoint_returns_404_for_unknown_request_id():
    client = TestClient(create_app())
    session_id = "local_asr_append_event_serializer_unknown_request_review"
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(session_id=session_id),
    )

    response = client.post(
        (
            f"/live/asr/sessions/{session_id}"
            "/llm-card-lifecycle-append-event-serializer-dry-runs"
        ),
        json={
            "mode": "dry_run_only",
            "request_id": "missing_request_id",
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 404
    assert (
        "LLM request draft not found for card lifecycle preview dry-run: missing_request_id"
        in response.text
    )


def test_asr_live_llm_card_lifecycle_append_event_serializer_dry_run_endpoint_reads_persisted_record_across_app_instances(
    tmp_path,
):
    session_id = "persisted_asr_append_event_serializer_review"
    first_client = TestClient(create_app(data_dir=tmp_path))
    create_response = first_client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(session_id=session_id),
    )

    second_client = TestClient(create_app(data_dir=tmp_path))
    record_path = tmp_path / "live_asr_sessions" / f"{session_id}.json"
    record_before = record_path.read_bytes()
    response = second_client.post(
        (
            f"/live/asr/sessions/{session_id}"
            "/llm-card-lifecycle-append-event-serializer-dry-runs"
        ),
        json={
            "mode": "dry_run_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    assert record_path.read_bytes() == record_before
    body = response.json()
    assert body["session_id"] == session_id
    assert body["append_event_serializer_status"] == "serialized"
    assert body["serialized_append_events"][0]["idempotency_key"] == (
        _card_lifecycle_append_idempotency_key(session_id, "llm_schema_result")
    )


def test_asr_live_llm_card_lifecycle_append_event_serializer_dry_run_endpoint_returns_404_for_missing_session():
    client = TestClient(create_app())

    response = client.post(
        (
            "/live/asr/sessions/missing_asr_append_event_serializer_review"
            "/llm-card-lifecycle-append-event-serializer-dry-runs"
        ),
        json={
            "mode": "dry_run_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert response.status_code == 404
    assert (
        "ASR live session not found: missing_asr_append_event_serializer_review"
        in response.text
    )


def test_asr_live_llm_card_lifecycle_append_event_serializer_dry_run_endpoint_rejects_request_shape_errors(
    monkeypatch,
    tmp_path,
):
    forbidden_values = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "append_event_serializer_shape",
    )
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(
            session_id="local_asr_append_event_serializer_shape_review"
        ),
    )
    path = (
        "/live/asr/sessions/local_asr_append_event_serializer_shape_review"
        "/llm-card-lifecycle-append-event-serializer-dry-runs"
    )

    cases = [
        ([], "request body must be an object"),
        ({}, "missing mode"),
        (
            {
                "mode": 123,
                "request_id": "request",
                "candidate_response": {},
            },
            "mode must be a string",
        ),
        (
            {
                "mode": "dry_run_only",
                "request_id": 123,
                "candidate_response": {},
            },
            "request_id must be a string",
        ),
        (
            {
                "mode": "enabled",
                "request_id": "request",
                "candidate_response": {},
            },
            (
                "unsupported card lifecycle append event serializer "
                "dry-run mode: enabled"
            ),
        ),
        (
            {
                "mode": " dry_run_only ",
                "request_id": "request",
                "candidate_response": {},
            },
            (
                "unsupported card lifecycle append event serializer "
                "dry-run mode:  dry_run_only "
            ),
        ),
        (
            {
                "mode": "\tdry_run_only",
                "request_id": "request",
                "candidate_response": {},
            },
            (
                "unsupported card lifecycle append event serializer "
                "dry-run mode: \tdry_run_only"
            ),
        ),
        (
            {
                "mode": "dry_run_only\n",
                "request_id": "request",
                "candidate_response": {},
            },
            (
                "unsupported card lifecycle append event serializer "
                "dry-run mode: dry_run_only\n"
            ),
        ),
        (
            {
                "mode": "",
                "request_id": "request",
                "candidate_response": {},
            },
            "missing mode",
        ),
        (
            {
                "mode": "dry_run_only",
                "candidate_response": {},
            },
            "missing request_id",
        ),
        (
            {
                "mode": "dry_run_only",
                "request_id": " ",
                "candidate_response": {},
            },
            "missing request_id",
        ),
        (
            {
                "mode": "dry_run_only",
                "request_id": "request",
            },
            "missing candidate_response",
        ),
        (
            {
                "mode": "dry_run_only",
                "request_id": "request",
                "candidate_response": [],
            },
            "candidate_response must be an object",
        ),
        (
            {
                "mode": "dry_run_only",
                "request_id": "request",
                "candidate_response": {},
                "api_key": "ignored-test-value",
            },
            "extra fields are not permitted: api_key",
        ),
    ]
    assert create_response.status_code == 201
    for payload, expected_detail in cases:
        response = client.post(path, json=payload)
        assert response.status_code == 422
        assert expected_detail in response.text or expected_detail in response.json()["detail"]
        for forbidden in forbidden_values:
            assert forbidden not in response.text


def test_asr_live_llm_card_lifecycle_append_mutation_preflights_endpoint_analyzes_allowed_events_without_mutating_events(
    monkeypatch,
    tmp_path,
):
    forbidden_values = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "append_mutation_preflight_allowed",
    )
    client = TestClient(create_app())
    session_id = "local_asr_append_mutation_preflight_allowed_review"
    request_id = (
        "asr_llm_request_draft_"
        "asr_suggestion_candidate_asr_state_event_asr_seg_001"
    )
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(session_id=session_id),
    )
    events_before_response = client.get(f"/live/asr/sessions/{session_id}/events")

    response = client.post(
        (
            f"/live/asr/sessions/{session_id}"
            "/llm-card-lifecycle-append-mutation-preflights"
        ),
        json={
            "mode": "preflight_only",
            "request_id": request_id,
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )
    events_after_response = client.get(f"/live/asr/sessions/{session_id}/events")

    assert create_response.status_code == 201
    assert events_before_response.status_code == 200
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == session_id
    assert body["append_event_serializer_status"] == "serialized"
    assert body["append_event_serialization_status"] == "would_serialize_if_enabled"
    assert body["append_mutation_preflight_mode"] == "preflight_only"
    assert body["append_mutation_preflight_status"] == "analyzed"
    assert body["append_mutation_readiness_status"] == "blocked_until_enabled"
    assert body["mutation_preflight_check_count"] == 2
    assert body["repository_transaction_status"] == "not_started"
    assert body["event_append_status"] == "not_appended"
    assert body["idempotency_store_status"] == "not_written"
    assert body["idempotency_store_write_status"] == "not_written"
    assert body["safe_to_mutate_events"] is False
    assert body["safe_to_commit_transaction"] is False
    assert body["safe_to_append_events"] is False
    assert body["safe_to_create_card"] is False

    serialized_events = body["serialized_append_events"]
    checks = body["mutation_preflight_checks"]
    assert [event["event_type"] for event in serialized_events] == [
        "llm_schema_result",
        "suggestion_card",
    ]
    assert [check["event_type"] for check in checks] == [
        "llm_schema_result",
        "suggestion_card",
    ]
    assert [check["mutation_preflight_check_id"] for check in checks] == [
        (
            "asr_card_lifecycle_append_mutation_preflight_"
            "llm_schema_result_card_dry_run_001"
        ),
        (
            "asr_card_lifecycle_append_mutation_preflight_"
            "suggestion_card_card_dry_run_001"
        ),
    ]
    serialized_events_by_id = {
        event["future_event_id"]: event for event in serialized_events
    }
    for check in checks:
        serialized_event = serialized_events_by_id[check["future_event_id"]]
        assert check["mutation_preflight_check_status"] == "blocked_until_enabled"
        assert check["serializer_result_id"] == serialized_event["serializer_result_id"]
        assert check["serialization_status"] == serialized_event["serialization_status"]
        assert check["future_event_id"] == serialized_event["future_event_id"]
        assert check["serialized_event_id"] == serialized_event["event_id"]
        assert check["preview_event_id"] == serialized_event["preview_event_id"]
        assert check["idempotency_key"] == serialized_event["idempotency_key"]
        assert check["would_append_sequence"] == serialized_event["would_append_sequence"]
        assert check["would_append_after_sequence"] == (
            serialized_event["would_append_after_sequence"]
        )
        assert check["append_status"] == serialized_event["append_status"]
        assert check["conflict_status"] == serialized_event["conflict_status"]
        assert check["repository_transaction_status"] == "not_started"
        assert check["event_append_status"] == "not_appended"
        assert check["idempotency_store_status"] == "not_written"
        assert check["idempotency_store_write_status"] == "not_written"
        assert check["safe_to_mutate_event"] is False
        assert check["safe_to_commit_transaction"] is False
        assert check["safe_to_append_event"] is False
        assert check["safe_to_create_card"] is False
    assert [check["idempotency_key"] for check in checks] == [
        _card_lifecycle_append_idempotency_key(session_id, "llm_schema_result"),
        _card_lifecycle_append_idempotency_key(session_id, "suggestion_card"),
    ]
    assert events_after_response.status_code == 200
    assert events_before_response.json()["events"] == events_after_response.json()["events"]
    assert events_after_response.json()["events"] == create_response.json()["live_events"]
    for forbidden in forbidden_values:
        assert forbidden not in response.text
    assert "llm_schema_result" not in [
        event["event_type"] for event in events_after_response.json()["events"]
    ]
    assert "suggestion_card" not in [
        event["event_type"] for event in events_after_response.json()["events"]
    ]


def test_asr_live_llm_card_lifecycle_append_mutation_preflights_endpoint_analyzes_schema_invalid_silenced_event():
    client = TestClient(create_app())
    session_id = "local_asr_append_mutation_preflight_schema_invalid_review"
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(session_id=session_id),
    )
    candidate = _valid_schema_validation_candidate_response()
    candidate["usage"] = {}
    candidate["schema_result"] = "failed"

    response = client.post(
        (
            f"/live/asr/sessions/{session_id}"
            "/llm-card-lifecycle-append-mutation-preflights"
        ),
        json={
            "mode": "preflight_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": candidate,
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["append_mutation_readiness_status"] == "blocked_until_enabled"
    assert [check["event_type"] for check in body["mutation_preflight_checks"]] == [
        "llm_schema_result",
        "suggestion_silenced",
    ]
    silenced_check = body["mutation_preflight_checks"][1]
    assert silenced_check["future_event_id"] == "suggestion_silenced:card_dry_run_001"
    assert silenced_check["mutation_preflight_check_status"] == "blocked_until_enabled"
    assert silenced_check["event_append_status"] == "not_appended"


def test_asr_live_llm_card_lifecycle_append_mutation_preflights_endpoint_analyzes_policy_blocked_silenced_event():
    client = TestClient(create_app())
    session_id = "local_asr_append_mutation_preflight_policy_blocked_review"
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id=session_id),
    )

    response = client.post(
        (
            f"/live/asr/sessions/{session_id}"
            "/llm-card-lifecycle-append-mutation-preflights"
        ),
        json={
            "mode": "preflight_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["append_mutation_readiness_status"] == "blocked_until_enabled"
    assert [check["event_type"] for check in body["mutation_preflight_checks"]] == [
        "llm_schema_result",
        "suggestion_silenced",
    ]
    silenced_check = body["mutation_preflight_checks"][1]
    assert silenced_check["future_event_id"] == "suggestion_silenced:card_dry_run_001"
    assert silenced_check["safe_to_mutate_event"] is False


def test_asr_live_llm_card_lifecycle_append_mutation_preflights_endpoint_preserves_serializer_preflight_conflicts(
    tmp_path,
):
    session_id = "persisted_asr_append_mutation_preflight_conflict_review"
    first_client = TestClient(create_app(data_dir=tmp_path))
    create_response = first_client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(session_id=session_id),
    )
    record_path = tmp_path / "live_asr_sessions" / f"{session_id}.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    _append_persisted_lifecycle_event(
        record,
        session_id=session_id,
        event_type="llm_schema_result",
        sequence=999,
    )
    record_path.write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )

    second_client = TestClient(create_app(data_dir=tmp_path))
    record_before = record_path.read_bytes()
    response = second_client.post(
        (
            f"/live/asr/sessions/{session_id}"
            "/llm-card-lifecycle-append-mutation-preflights"
        ),
        json={
            "mode": "preflight_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    assert record_path.read_bytes() == record_before
    body = response.json()
    assert body["append_preflight_status"] == "blocked"
    assert body["append_event_serialization_status"] == "blocked_by_preflight"
    assert body["append_mutation_readiness_status"] == (
        "blocked_by_serializer_preflight"
    )
    assert [check["mutation_preflight_check_status"] for check in body["mutation_preflight_checks"]] == [
        "blocked_by_serializer_preflight",
        "blocked_until_enabled",
    ]
    assert [check["serialization_status"] for check in body["mutation_preflight_checks"]] == [
        "blocked_by_preflight",
        "would_serialize_if_enabled",
    ]
    assert body["event_append_status"] == "not_appended"
    assert body["idempotency_store_status"] == "not_written"
    assert body["safe_to_mutate_events"] is False


def test_asr_live_llm_card_lifecycle_append_mutation_preflights_endpoint_returns_404_for_unknown_request_id():
    client = TestClient(create_app())
    session_id = "local_asr_append_mutation_preflight_unknown_request_review"
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(session_id=session_id),
    )

    response = client.post(
        (
            f"/live/asr/sessions/{session_id}"
            "/llm-card-lifecycle-append-mutation-preflights"
        ),
        json={
            "mode": "preflight_only",
            "request_id": "missing_request_id",
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 404
    assert (
        "LLM request draft not found for card lifecycle preview dry-run: missing_request_id"
        in response.text
    )


def test_asr_live_llm_card_lifecycle_append_mutation_preflights_endpoint_reads_persisted_record_across_app_instances(
    tmp_path,
):
    session_id = "persisted_asr_append_mutation_preflight_review"
    first_client = TestClient(create_app(data_dir=tmp_path))
    create_response = first_client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(session_id=session_id),
    )

    second_client = TestClient(create_app(data_dir=tmp_path))
    record_path = tmp_path / "live_asr_sessions" / f"{session_id}.json"
    record_before = record_path.read_bytes()
    response = second_client.post(
        (
            f"/live/asr/sessions/{session_id}"
            "/llm-card-lifecycle-append-mutation-preflights"
        ),
        json={
            "mode": "preflight_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    assert record_path.read_bytes() == record_before
    body = response.json()
    assert body["session_id"] == session_id
    assert body["append_mutation_preflight_status"] == "analyzed"
    assert body["mutation_preflight_checks"][0]["idempotency_key"] == (
        _card_lifecycle_append_idempotency_key(session_id, "llm_schema_result")
    )


def test_asr_live_llm_card_lifecycle_append_mutation_preflights_endpoint_returns_404_for_missing_session():
    client = TestClient(create_app())

    response = client.post(
        (
            "/live/asr/sessions/missing_asr_append_mutation_preflight_review"
            "/llm-card-lifecycle-append-mutation-preflights"
        ),
        json={
            "mode": "preflight_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert response.status_code == 404
    assert (
        "ASR live session not found: missing_asr_append_mutation_preflight_review"
        in response.text
    )


def test_asr_live_llm_card_lifecycle_append_mutation_preflights_endpoint_rejects_request_shape_errors(
    monkeypatch,
    tmp_path,
):
    forbidden_values = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "append_mutation_preflight_shape",
    )
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(
            session_id="local_asr_append_mutation_preflight_shape_review"
        ),
    )
    path = (
        "/live/asr/sessions/local_asr_append_mutation_preflight_shape_review"
        "/llm-card-lifecycle-append-mutation-preflights"
    )

    cases = [
        ([], "request body must be an object"),
        ({}, "missing mode"),
        (
            {
                "mode": 123,
                "request_id": "request",
                "candidate_response": {},
            },
            "mode must be a string",
        ),
        (
            {
                "mode": "preflight_only",
                "request_id": 123,
                "candidate_response": {},
            },
            "request_id must be a string",
        ),
        (
            {
                "mode": "enabled",
                "request_id": "request",
                "candidate_response": {},
            },
            "unsupported card lifecycle append mutation preflight mode: enabled",
        ),
        (
            {
                "mode": " preflight_only ",
                "request_id": "request",
                "candidate_response": {},
            },
            (
                "unsupported card lifecycle append mutation preflight "
                "mode:  preflight_only "
            ),
        ),
        (
            {
                "mode": "\tpreflight_only",
                "request_id": "request",
                "candidate_response": {},
            },
            (
                "unsupported card lifecycle append mutation preflight "
                "mode: \tpreflight_only"
            ),
        ),
        (
            {
                "mode": "preflight_only\n",
                "request_id": "request",
                "candidate_response": {},
            },
            (
                "unsupported card lifecycle append mutation preflight "
                "mode: preflight_only\n"
            ),
        ),
        (
            {
                "mode": "",
                "request_id": "request",
                "candidate_response": {},
            },
            "missing mode",
        ),
        (
            {
                "mode": "preflight_only",
                "candidate_response": {},
            },
            "missing request_id",
        ),
        (
            {
                "mode": "preflight_only",
                "request_id": " ",
                "candidate_response": {},
            },
            "missing request_id",
        ),
        (
            {
                "mode": "preflight_only",
                "request_id": "request",
            },
            "missing candidate_response",
        ),
        (
            {
                "mode": "preflight_only",
                "request_id": "request",
                "candidate_response": [],
            },
            "candidate_response must be an object",
        ),
        (
            {
                "mode": "preflight_only",
                "request_id": "request",
                "candidate_response": {},
                "api_key": "ignored-test-value",
            },
            "extra fields are not permitted: api_key",
        ),
    ]
    assert create_response.status_code == 201
    for payload, expected_detail in cases:
        response = client.post(path, json=payload)
        assert response.status_code == 422
        assert expected_detail in response.text or expected_detail in response.json()["detail"]
        for forbidden in forbidden_values:
            assert forbidden not in response.text


def test_asr_live_llm_card_lifecycle_append_transaction_commit_preflights_endpoint_blocks_fresh_append_until_enabled_without_mutating_events(
    monkeypatch,
    tmp_path,
):
    forbidden_values = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "append_transaction_commit_preflight_fresh",
    )
    client = TestClient(create_app())
    session_id = "local_asr_append_transaction_commit_preflight_fresh_review"
    request_id = (
        "asr_llm_request_draft_"
        "asr_suggestion_candidate_asr_state_event_asr_seg_001"
    )
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(session_id=session_id),
    )
    events_before_response = client.get(f"/live/asr/sessions/{session_id}/events")

    response = client.post(
        (
            f"/live/asr/sessions/{session_id}"
            "/llm-card-lifecycle-append-transaction-commit-preflights"
        ),
        json={
            "mode": "preflight_only",
            "request_id": request_id,
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )
    events_after_response = client.get(f"/live/asr/sessions/{session_id}/events")

    assert create_response.status_code == 201
    assert events_before_response.status_code == 200
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == session_id
    assert body["append_mutation_preflight_status"] == "analyzed"
    assert body["retry_replay_preflight_status"] == "analyzed"
    assert body["retry_replay_resolution_status"] == "no_existing_append"
    assert body["append_transaction_commit_preflight_mode"] == "preflight_only"
    assert body["append_transaction_commit_preflight_status"] == "analyzed"
    assert body["transaction_commit_readiness_status"] == "blocked_until_enabled"
    assert body["transaction_commit_preflight_check_count"] == 2
    assert body["repository_transaction_status"] == "not_started"
    assert body["repository_transaction_commit_status"] == "not_committed"
    assert body["repository_transaction_rollback_status"] == "not_started"
    assert body["event_append_status"] == "not_appended"
    assert body["audit_event_append_status"] == "not_appended"
    assert body["idempotency_store_status"] == "not_written"
    assert body["idempotency_store_write_status"] == "not_written"
    assert body["safe_to_begin_transaction"] is False
    assert body["safe_to_commit_transaction"] is False
    assert body["safe_to_rollback_transaction"] is False
    assert body["safe_to_mutate_events"] is False
    assert body["safe_to_append_events"] is False
    assert body["safe_to_write_idempotency_store"] is False
    assert body["safe_to_write_audit_events"] is False
    assert body["safe_to_create_card"] is False

    checks = body["transaction_commit_preflight_checks"]
    assert [check["event_type"] for check in checks] == [
        "llm_schema_result",
        "suggestion_card",
    ]
    assert [check["transaction_commit_preflight_check_id"] for check in checks] == [
        (
            "asr_card_lifecycle_append_transaction_commit_preflight_"
            "llm_schema_result_card_dry_run_001"
        ),
        (
            "asr_card_lifecycle_append_transaction_commit_preflight_"
            "suggestion_card_card_dry_run_001"
        ),
    ]
    mutation_checks_by_event = {
        check["future_event_id"]: check for check in body["mutation_preflight_checks"]
    }
    retry_checks_by_event = {
        check["future_event_id"]: check for check in body["retry_replay_checks"]
    }
    for check in checks:
        mutation_check = mutation_checks_by_event[check["future_event_id"]]
        retry_check = retry_checks_by_event[check["future_event_id"]]
        assert check["transaction_commit_preflight_check_status"] == (
            "blocked_until_enabled"
        )
        assert check["mutation_preflight_check_id"] == (
            mutation_check["mutation_preflight_check_id"]
        )
        assert check["mutation_preflight_check_status"] == (
            mutation_check["mutation_preflight_check_status"]
        )
        assert check["retry_replay_check_id"] == retry_check["retry_replay_check_id"]
        assert check["retry_replay_resolution_status"] == "no_existing_append"
        assert check["serializer_result_id"] == mutation_check["serializer_result_id"]
        assert check["serialization_status"] == mutation_check["serialization_status"]
        assert check["future_event_id"] == mutation_check["future_event_id"]
        assert check["serialized_event_id"] == mutation_check["serialized_event_id"]
        assert check["preview_event_id"] == mutation_check["preview_event_id"]
        assert check["idempotency_key"] == mutation_check["idempotency_key"]
        assert check["transaction_idempotency_key"] == (
            retry_check["transaction_idempotency_key"]
        )
        assert check["would_append_sequence"] == mutation_check["would_append_sequence"]
        assert check["would_append_after_sequence"] == (
            mutation_check["would_append_after_sequence"]
        )
        assert check["append_status"] == mutation_check["append_status"]
        assert check["conflict_status"] == mutation_check["conflict_status"]
        assert check["repository_transaction_status"] == "not_started"
        assert check["repository_transaction_commit_status"] == "not_committed"
        assert check["repository_transaction_rollback_status"] == "not_started"
        assert check["event_append_status"] == "not_appended"
        assert check["audit_event_append_status"] == "not_appended"
        assert check["idempotency_store_status"] == "not_written"
        assert check["idempotency_store_write_status"] == "not_written"
        assert check["safe_to_begin_transaction"] is False
        assert check["safe_to_commit_transaction"] is False
        assert check["safe_to_rollback_transaction"] is False
        assert check["safe_to_append_event"] is False
        assert check["safe_to_write_idempotency_store"] is False
        assert check["safe_to_write_audit_event"] is False
        assert check["safe_to_create_card"] is False
    assert events_after_response.status_code == 200
    assert events_before_response.json()["events"] == events_after_response.json()["events"]
    assert events_after_response.json()["events"] == create_response.json()["live_events"]
    for forbidden in forbidden_values:
        assert forbidden not in response.text
    assert "llm_schema_result" not in [
        event["event_type"] for event in events_after_response.json()["events"]
    ]
    assert "suggestion_card" not in [
        event["event_type"] for event in events_after_response.json()["events"]
    ]


def test_asr_live_llm_card_lifecycle_append_transaction_commit_preflights_endpoint_treats_complete_replay_as_non_mutating_replay(
    tmp_path,
):
    session_id = "persisted_asr_append_transaction_commit_preflight_replay_review"
    first_client = TestClient(create_app(data_dir=tmp_path))
    create_response = first_client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(session_id=session_id),
    )
    record_path = tmp_path / "live_asr_sessions" / f"{session_id}.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    _append_persisted_lifecycle_event(
        record,
        session_id=session_id,
        event_type="llm_schema_result",
        sequence=999,
    )
    _append_persisted_lifecycle_event(
        record,
        session_id=session_id,
        event_type="suggestion_card",
        sequence=1000,
    )
    record_path.write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )

    second_client = TestClient(create_app(data_dir=tmp_path))
    record_before = record_path.read_bytes()
    response = second_client.post(
        (
            f"/live/asr/sessions/{session_id}"
            "/llm-card-lifecycle-append-transaction-commit-preflights"
        ),
        json={
            "mode": "preflight_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    assert record_path.read_bytes() == record_before
    body = response.json()
    assert body["retry_replay_resolution_status"] == "safe_to_replay"
    assert body["append_mutation_readiness_status"] == (
        "blocked_by_serializer_preflight"
    )
    assert body["transaction_commit_readiness_status"] == (
        "safe_replay_existing_events"
    )
    assert body["safe_to_begin_transaction"] is False
    assert body["safe_to_commit_transaction"] is False
    assert body["safe_to_append_events"] is False
    assert body["repository_transaction_commit_status"] == "not_committed"
    assert [check["transaction_commit_preflight_check_status"] for check in body["transaction_commit_preflight_checks"]] == [
        "safe_replay_existing_event",
        "safe_replay_existing_event",
    ]
    assert [check["retry_replay_resolution_status"] for check in body["transaction_commit_preflight_checks"]] == [
        "safe_replay_same_event",
        "safe_replay_same_event",
    ]
    assert {check["mutation_preflight_check_status"] for check in body["transaction_commit_preflight_checks"]} == {
        "blocked_by_serializer_preflight"
    }


def test_asr_live_llm_card_lifecycle_append_transaction_commit_preflights_endpoint_blocks_partial_replay_without_filling_missing_tail(
    tmp_path,
):
    session_id = "persisted_asr_append_transaction_commit_preflight_partial_review"
    first_client = TestClient(create_app(data_dir=tmp_path))
    create_response = first_client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(session_id=session_id),
    )
    record_path = tmp_path / "live_asr_sessions" / f"{session_id}.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    _append_persisted_lifecycle_event(
        record,
        session_id=session_id,
        event_type="llm_schema_result",
        sequence=999,
    )
    record_path.write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )

    second_client = TestClient(create_app(data_dir=tmp_path))
    events_before_response = second_client.get(f"/live/asr/sessions/{session_id}/events")
    record_before = record_path.read_bytes()
    response = second_client.post(
        (
            f"/live/asr/sessions/{session_id}"
            "/llm-card-lifecycle-append-transaction-commit-preflights"
        ),
        json={
            "mode": "preflight_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )
    events_after_response = second_client.get(f"/live/asr/sessions/{session_id}/events")

    assert create_response.status_code == 201
    assert events_before_response.status_code == 200
    assert response.status_code == 200
    assert record_path.read_bytes() == record_before
    body = response.json()
    assert body["retry_replay_resolution_status"] == "blocked_by_partial_replay"
    assert body["transaction_commit_readiness_status"] == "blocked_by_partial_replay"
    assert [check["transaction_commit_preflight_check_status"] for check in body["transaction_commit_preflight_checks"]] == [
        "safe_replay_existing_event",
        "blocked_by_partial_replay",
    ]
    assert [check["retry_replay_resolution_status"] for check in body["transaction_commit_preflight_checks"]] == [
        "safe_replay_same_event",
        "blocked_partial_replay",
    ]
    assert body["safe_to_commit_transaction"] is False
    assert body["safe_to_append_events"] is False
    assert body["event_append_status"] == "not_appended"
    assert events_before_response.json()["events"] == events_after_response.json()["events"]


def test_asr_live_llm_card_lifecycle_append_transaction_commit_preflights_endpoint_blocks_retry_replay_conflicts(
    tmp_path,
):
    session_id = "persisted_asr_append_transaction_commit_preflight_conflict_review"
    first_client = TestClient(create_app(data_dir=tmp_path))
    create_response = first_client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(session_id=session_id),
    )
    record_path = tmp_path / "live_asr_sessions" / f"{session_id}.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    _append_persisted_lifecycle_event(
        record,
        session_id=session_id,
        event_type="llm_schema_result",
        sequence=999,
        payload_extra={"request_id": "different_request"},
    )
    _append_persisted_lifecycle_event(
        record,
        session_id=session_id,
        event_type="suggestion_card",
        sequence=1000,
        payload_extra={"card": {"id": "different_nested_card_id"}},
    )
    record_path.write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )

    second_client = TestClient(create_app(data_dir=tmp_path))
    record_before = record_path.read_bytes()
    response = second_client.post(
        (
            f"/live/asr/sessions/{session_id}"
            "/llm-card-lifecycle-append-transaction-commit-preflights"
        ),
        json={
            "mode": "preflight_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    assert record_path.read_bytes() == record_before
    body = response.json()
    assert body["retry_replay_resolution_status"] == "blocked_by_conflict"
    assert body["transaction_commit_readiness_status"] == (
        "blocked_by_retry_replay_conflict"
    )
    assert [check["transaction_commit_preflight_check_status"] for check in body["transaction_commit_preflight_checks"]] == [
        "blocked_by_retry_replay_conflict",
        "blocked_by_retry_replay_conflict",
    ]
    assert [check["retry_replay_resolution_status"] for check in body["transaction_commit_preflight_checks"]] == [
        "blocked_mismatched_replay",
        "blocked_mismatched_replay",
    ]
    assert body["safe_to_commit_transaction"] is False
    assert body["event_append_status"] == "not_appended"


def test_asr_live_llm_card_lifecycle_append_transaction_commit_preflights_endpoint_analyzes_schema_invalid_silenced_events():
    client = TestClient(create_app())
    session_id = "local_asr_append_transaction_commit_preflight_schema_invalid_review"
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(session_id=session_id),
    )
    candidate = _valid_schema_validation_candidate_response()
    candidate["usage"] = {}
    candidate["schema_result"] = "failed"

    response = client.post(
        (
            f"/live/asr/sessions/{session_id}"
            "/llm-card-lifecycle-append-transaction-commit-preflights"
        ),
        json={
            "mode": "preflight_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": candidate,
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["transaction_commit_readiness_status"] == "blocked_until_enabled"
    assert [check["event_type"] for check in body["transaction_commit_preflight_checks"]] == [
        "llm_schema_result",
        "suggestion_silenced",
    ]
    assert body["transaction_commit_preflight_checks"][1]["future_event_id"] == (
        "suggestion_silenced:card_dry_run_001"
    )
    assert body["safe_to_commit_transaction"] is False


def test_asr_live_llm_card_lifecycle_append_transaction_commit_preflights_endpoint_analyzes_policy_blocked_silenced_events():
    client = TestClient(create_app())
    session_id = "local_asr_append_transaction_commit_preflight_policy_blocked_review"
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id=session_id),
    )

    response = client.post(
        (
            f"/live/asr/sessions/{session_id}"
            "/llm-card-lifecycle-append-transaction-commit-preflights"
        ),
        json={
            "mode": "preflight_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["transaction_commit_readiness_status"] == "blocked_until_enabled"
    assert [check["event_type"] for check in body["transaction_commit_preflight_checks"]] == [
        "llm_schema_result",
        "suggestion_silenced",
    ]
    assert body["transaction_commit_preflight_checks"][1]["future_event_id"] == (
        "suggestion_silenced:card_dry_run_001"
    )
    assert body["safe_to_commit_transaction"] is False


def test_asr_live_llm_card_lifecycle_append_transaction_commit_preflights_endpoint_returns_404_for_unknown_request_id():
    client = TestClient(create_app())
    session_id = "local_asr_append_transaction_commit_preflight_unknown_request_review"
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(session_id=session_id),
    )

    response = client.post(
        (
            f"/live/asr/sessions/{session_id}"
            "/llm-card-lifecycle-append-transaction-commit-preflights"
        ),
        json={
            "mode": "preflight_only",
            "request_id": "missing_request_id",
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 404
    assert (
        "LLM request draft not found for card lifecycle preview dry-run: missing_request_id"
        in response.text
    )


def test_asr_live_llm_card_lifecycle_append_transaction_commit_preflights_endpoint_reads_persisted_record_across_app_instances(
    tmp_path,
):
    session_id = "persisted_asr_append_transaction_commit_preflight_review"
    first_client = TestClient(create_app(data_dir=tmp_path))
    create_response = first_client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(session_id=session_id),
    )

    second_client = TestClient(create_app(data_dir=tmp_path))
    record_path = tmp_path / "live_asr_sessions" / f"{session_id}.json"
    record_before = record_path.read_bytes()
    response = second_client.post(
        (
            f"/live/asr/sessions/{session_id}"
            "/llm-card-lifecycle-append-transaction-commit-preflights"
        ),
        json={
            "mode": "preflight_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    assert record_path.read_bytes() == record_before
    body = response.json()
    assert body["session_id"] == session_id
    assert body["append_transaction_commit_preflight_status"] == "analyzed"
    assert body["transaction_commit_preflight_checks"][0]["idempotency_key"] == (
        _card_lifecycle_append_idempotency_key(session_id, "llm_schema_result")
    )


def test_asr_live_llm_card_lifecycle_append_transaction_commit_preflights_endpoint_returns_404_for_missing_session():
    client = TestClient(create_app())

    response = client.post(
        (
            "/live/asr/sessions/missing_asr_append_transaction_commit_preflight_review"
            "/llm-card-lifecycle-append-transaction-commit-preflights"
        ),
        json={
            "mode": "preflight_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert response.status_code == 404
    assert (
        "ASR live session not found: missing_asr_append_transaction_commit_preflight_review"
        in response.text
    )


def test_asr_live_llm_card_lifecycle_append_transaction_commit_preflights_endpoint_rejects_request_shape_errors(
    monkeypatch,
    tmp_path,
):
    forbidden_values = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "append_transaction_commit_preflight_shape",
    )
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(
            session_id="local_asr_append_transaction_commit_preflight_shape_review"
        ),
    )
    path = (
        "/live/asr/sessions/local_asr_append_transaction_commit_preflight_shape_review"
        "/llm-card-lifecycle-append-transaction-commit-preflights"
    )

    cases = [
        ([], "request body must be an object"),
        ({}, "missing mode"),
        (
            {
                "mode": 123,
                "request_id": "request",
                "candidate_response": {},
            },
            "mode must be a string",
        ),
        (
            {
                "mode": "preflight_only",
                "request_id": 123,
                "candidate_response": {},
            },
            "request_id must be a string",
        ),
        (
            {
                "mode": "enabled",
                "request_id": "request",
                "candidate_response": {},
            },
            (
                "unsupported card lifecycle append transaction commit "
                "preflight mode: enabled"
            ),
        ),
        (
            {
                "mode": " preflight_only ",
                "request_id": "request",
                "candidate_response": {},
            },
            (
                "unsupported card lifecycle append transaction commit "
                "preflight mode:  preflight_only "
            ),
        ),
        (
            {
                "mode": "",
                "request_id": "request",
                "candidate_response": {},
            },
            "missing mode",
        ),
        (
            {
                "mode": "preflight_only",
                "candidate_response": {},
            },
            "missing request_id",
        ),
        (
            {
                "mode": "preflight_only",
                "request_id": " ",
                "candidate_response": {},
            },
            "missing request_id",
        ),
        (
            {
                "mode": "preflight_only",
                "request_id": "request",
            },
            "missing candidate_response",
        ),
        (
            {
                "mode": "preflight_only",
                "request_id": "request",
                "candidate_response": [],
            },
            "candidate_response must be an object",
        ),
        (
            {
                "mode": "preflight_only",
                "request_id": "request",
                "candidate_response": {},
                "api_key": "ignored-test-value",
            },
            "extra fields are not permitted: api_key",
        ),
    ]
    assert create_response.status_code == 201
    for payload, expected_detail in cases:
        response = client.post(path, json=payload)
        assert response.status_code == 422
        assert expected_detail in response.text or expected_detail in response.json()["detail"]
        for forbidden in forbidden_values:
            assert forbidden not in response.text


def test_asr_live_llm_card_lifecycle_append_idempotency_store_write_preflights_endpoint_blocks_fresh_append_until_enabled_without_mutating_events(
    monkeypatch,
    tmp_path,
):
    forbidden_values = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "append_idempotency_store_write_preflight_fresh",
    )
    client = TestClient(create_app())
    session_id = "local_asr_append_idempotency_store_write_preflight_fresh_review"
    request_id = (
        "asr_llm_request_draft_"
        "asr_suggestion_candidate_asr_state_event_asr_seg_001"
    )
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(session_id=session_id),
    )
    events_before_response = client.get(f"/live/asr/sessions/{session_id}/events")

    response = client.post(
        (
            f"/live/asr/sessions/{session_id}"
            "/llm-card-lifecycle-append-idempotency-store-write-preflights"
        ),
        json={
            "mode": "preflight_only",
            "request_id": request_id,
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )
    events_after_response = client.get(f"/live/asr/sessions/{session_id}/events")

    assert create_response.status_code == 201
    assert events_before_response.status_code == 200
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == session_id
    assert body["append_transaction_commit_preflight_status"] == "analyzed"
    assert body["transaction_commit_readiness_status"] == "blocked_until_enabled"
    assert body["idempotency_store_write_preflight_mode"] == "preflight_only"
    assert body["idempotency_store_write_preflight_status"] == "analyzed"
    assert body["idempotency_store_write_readiness_status"] == "blocked_until_enabled"
    assert body["idempotency_store_write_preflight_check_count"] == 2
    assert body["idempotency_store_status"] == "not_written"
    assert body["idempotency_store_write_status"] == "not_written"
    assert body["repository_transaction_status"] == "not_started"
    assert body["repository_transaction_commit_status"] == "not_committed"
    assert body["repository_transaction_rollback_status"] == "not_started"
    assert body["event_append_status"] == "not_appended"
    assert body["audit_event_append_status"] == "not_appended"
    assert body["safe_to_write_idempotency_store"] is False
    assert body["safe_to_begin_transaction"] is False
    assert body["safe_to_commit_transaction"] is False
    assert body["safe_to_rollback_transaction"] is False
    assert body["safe_to_mutate_events"] is False
    assert body["safe_to_append_events"] is False
    assert body["safe_to_write_audit_events"] is False
    assert body["safe_to_create_card"] is False

    checks = body["idempotency_store_write_preflight_checks"]
    assert [check["event_type"] for check in checks] == [
        "llm_schema_result",
        "suggestion_card",
    ]
    assert [check["idempotency_store_write_preflight_check_id"] for check in checks] == [
        (
            "asr_card_lifecycle_append_idempotency_store_write_preflight_"
            "llm_schema_result_card_dry_run_001"
        ),
        (
            "asr_card_lifecycle_append_idempotency_store_write_preflight_"
            "suggestion_card_card_dry_run_001"
        ),
    ]
    transaction_checks_by_event = {
        check["future_event_id"]: check
        for check in body["transaction_commit_preflight_checks"]
    }
    for check in checks:
        transaction_check = transaction_checks_by_event[check["future_event_id"]]
        assert check["idempotency_store_write_preflight_check_status"] == (
            "blocked_until_enabled"
        )
        assert check["future_idempotency_record_id"] == (
            "asr_card_lifecycle_append_idempotency_record_"
            f"{check['event_type']}_card_dry_run_001"
        )
        assert check["future_idempotency_record_key"] == transaction_check["idempotency_key"]
        assert check["future_idempotency_record_status"] == "would_write_if_enabled"
        assert check["idempotency_store_write_reason"] == (
            "fresh_append_requires_idempotency_record"
        )
        assert check["transaction_commit_preflight_check_id"] == (
            transaction_check["transaction_commit_preflight_check_id"]
        )
        assert check["transaction_commit_preflight_check_status"] == (
            transaction_check["transaction_commit_preflight_check_status"]
        )
        assert check["mutation_preflight_check_id"] == (
            transaction_check["mutation_preflight_check_id"]
        )
        assert check["retry_replay_check_id"] == (
            transaction_check["retry_replay_check_id"]
        )
        assert check["retry_replay_resolution_status"] == "no_existing_append"
        assert check["serializer_result_id"] == transaction_check["serializer_result_id"]
        assert check["serialization_status"] == transaction_check["serialization_status"]
        assert check["serialized_event_id"] == transaction_check["serialized_event_id"]
        assert check["preview_event_id"] == transaction_check["preview_event_id"]
        assert check["idempotency_key"] == transaction_check["idempotency_key"]
        assert check["transaction_idempotency_key"] == (
            transaction_check["transaction_idempotency_key"]
        )
        assert check["would_append_sequence"] == transaction_check["would_append_sequence"]
        assert check["would_append_after_sequence"] == (
            transaction_check["would_append_after_sequence"]
        )
        assert check["append_status"] == transaction_check["append_status"]
        assert check["conflict_status"] == transaction_check["conflict_status"]
        assert check["repository_transaction_status"] == "not_started"
        assert check["repository_transaction_commit_status"] == "not_committed"
        assert check["repository_transaction_rollback_status"] == "not_started"
        assert check["event_append_status"] == "not_appended"
        assert check["audit_event_append_status"] == "not_appended"
        assert check["idempotency_store_status"] == "not_written"
        assert check["idempotency_store_write_status"] == "not_written"
        assert check["safe_to_write_idempotency_store"] is False
        assert check["safe_to_begin_transaction"] is False
        assert check["safe_to_commit_transaction"] is False
        assert check["safe_to_rollback_transaction"] is False
        assert check["safe_to_append_event"] is False
        assert check["safe_to_write_audit_event"] is False
        assert check["safe_to_create_card"] is False
    assert events_after_response.status_code == 200
    assert events_before_response.json()["events"] == events_after_response.json()["events"]
    assert events_after_response.json()["events"] == create_response.json()["live_events"]
    for forbidden in forbidden_values:
        assert forbidden not in response.text
    assert "llm_schema_result" not in [
        event["event_type"] for event in events_after_response.json()["events"]
    ]
    assert "suggestion_card" not in [
        event["event_type"] for event in events_after_response.json()["events"]
    ]


def test_asr_live_llm_card_lifecycle_append_idempotency_store_write_preflights_endpoint_treats_complete_replay_as_no_write_replay(
    tmp_path,
):
    session_id = "persisted_asr_append_idempotency_store_write_preflight_replay_review"
    first_client = TestClient(create_app(data_dir=tmp_path))
    create_response = first_client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(session_id=session_id),
    )
    record_path = tmp_path / "live_asr_sessions" / f"{session_id}.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    _append_persisted_lifecycle_event(
        record,
        session_id=session_id,
        event_type="llm_schema_result",
        sequence=999,
    )
    _append_persisted_lifecycle_event(
        record,
        session_id=session_id,
        event_type="suggestion_card",
        sequence=1000,
    )
    record_path.write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )

    second_client = TestClient(create_app(data_dir=tmp_path))
    record_before = record_path.read_bytes()
    response = second_client.post(
        (
            f"/live/asr/sessions/{session_id}"
            "/llm-card-lifecycle-append-idempotency-store-write-preflights"
        ),
        json={
            "mode": "preflight_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    assert record_path.read_bytes() == record_before
    body = response.json()
    assert body["transaction_commit_readiness_status"] == (
        "safe_replay_existing_events"
    )
    assert body["idempotency_store_write_readiness_status"] == (
        "safe_replay_existing_events"
    )
    assert body["safe_to_write_idempotency_store"] is False
    assert body["safe_to_commit_transaction"] is False
    assert body["idempotency_store_write_status"] == "not_written"
    assert [check["idempotency_store_write_preflight_check_status"] for check in body["idempotency_store_write_preflight_checks"]] == [
        "write_not_required_for_safe_replay",
        "write_not_required_for_safe_replay",
    ]
    assert [check["future_idempotency_record_status"] for check in body["idempotency_store_write_preflight_checks"]] == [
        "not_required_existing_replay",
        "not_required_existing_replay",
    ]
    assert {check["idempotency_store_write_reason"] for check in body["idempotency_store_write_preflight_checks"]} == {
        "safe_replay_existing_event_requires_no_new_record"
    }


def test_asr_live_llm_card_lifecycle_append_idempotency_store_write_preflights_endpoint_blocks_partial_replay_without_store_tail_write(
    tmp_path,
):
    session_id = "persisted_asr_append_idempotency_store_write_preflight_partial_review"
    first_client = TestClient(create_app(data_dir=tmp_path))
    create_response = first_client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(session_id=session_id),
    )
    record_path = tmp_path / "live_asr_sessions" / f"{session_id}.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    _append_persisted_lifecycle_event(
        record,
        session_id=session_id,
        event_type="llm_schema_result",
        sequence=999,
    )
    record_path.write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )

    second_client = TestClient(create_app(data_dir=tmp_path))
    events_before_response = second_client.get(f"/live/asr/sessions/{session_id}/events")
    record_before = record_path.read_bytes()
    response = second_client.post(
        (
            f"/live/asr/sessions/{session_id}"
            "/llm-card-lifecycle-append-idempotency-store-write-preflights"
        ),
        json={
            "mode": "preflight_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )
    events_after_response = second_client.get(f"/live/asr/sessions/{session_id}/events")

    assert create_response.status_code == 201
    assert response.status_code == 200
    assert record_path.read_bytes() == record_before
    body = response.json()
    assert body["idempotency_store_write_readiness_status"] == (
        "blocked_by_partial_replay"
    )
    assert [check["idempotency_store_write_preflight_check_status"] for check in body["idempotency_store_write_preflight_checks"]] == [
        "write_not_required_for_safe_replay",
        "blocked_by_partial_replay",
    ]
    assert [check["future_idempotency_record_status"] for check in body["idempotency_store_write_preflight_checks"]] == [
        "not_required_existing_replay",
        "blocked",
    ]
    assert all(
        check["safe_to_write_idempotency_store"] is False
        for check in body["idempotency_store_write_preflight_checks"]
    )
    assert body["idempotency_store_write_status"] == "not_written"
    assert events_before_response.json()["events"] == events_after_response.json()["events"]


def test_asr_live_llm_card_lifecycle_append_idempotency_store_write_preflights_endpoint_blocks_retry_replay_conflicts(
    tmp_path,
):
    session_id = "persisted_asr_append_idempotency_store_write_preflight_conflict_review"
    first_client = TestClient(create_app(data_dir=tmp_path))
    create_response = first_client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(session_id=session_id),
    )
    record_path = tmp_path / "live_asr_sessions" / f"{session_id}.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    _append_persisted_lifecycle_event(
        record,
        session_id=session_id,
        event_type="llm_schema_result",
        sequence=999,
        payload_extra={"request_id": "different_request"},
    )
    _append_persisted_lifecycle_event(
        record,
        session_id=session_id,
        event_type="suggestion_card",
        sequence=1000,
        payload_extra={"card": {"id": "different_nested_card_id"}},
    )
    record_path.write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )

    second_client = TestClient(create_app(data_dir=tmp_path))
    record_before = record_path.read_bytes()
    response = second_client.post(
        (
            f"/live/asr/sessions/{session_id}"
            "/llm-card-lifecycle-append-idempotency-store-write-preflights"
        ),
        json={
            "mode": "preflight_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    assert record_path.read_bytes() == record_before
    body = response.json()
    assert body["transaction_commit_readiness_status"] == (
        "blocked_by_retry_replay_conflict"
    )
    assert body["idempotency_store_write_readiness_status"] == (
        "blocked_by_retry_replay_conflict"
    )
    assert [check["idempotency_store_write_preflight_check_status"] for check in body["idempotency_store_write_preflight_checks"]] == [
        "blocked_by_retry_replay_conflict",
        "blocked_by_retry_replay_conflict",
    ]
    assert {check["future_idempotency_record_status"] for check in body["idempotency_store_write_preflight_checks"]} == {
        "blocked"
    }
    assert body["safe_to_write_idempotency_store"] is False
    assert body["event_append_status"] == "not_appended"


def test_asr_live_llm_card_lifecycle_append_idempotency_store_write_preflights_endpoint_preserves_transaction_readiness_source_when_commit_preflight_blocks(
    monkeypatch,
):
    captured_payload = {}

    def fake_transaction_commit_preflight(record, payload):
        captured_payload.update(payload)
        return {
            "session_id": str(record["session_id"]),
            "source": str(record["source"]),
            "trace_kind": str(record["trace_kind"]),
            "append_transaction_commit_preflight_status": "analyzed",
            "transaction_commit_readiness_status": "blocked_by_mutation_preflight",
            "transaction_commit_preflight_check_count": 1,
            "transaction_commit_preflight_checks": [
                {
                    "transaction_commit_preflight_check_id": (
                        "asr_card_lifecycle_append_transaction_commit_preflight_"
                        "llm_schema_result_card_dry_run_001"
                    ),
                    "transaction_commit_preflight_check_status": (
                        "blocked_by_mutation_preflight"
                    ),
                    "mutation_preflight_check_id": (
                        "asr_card_lifecycle_append_mutation_preflight_"
                        "llm_schema_result_card_dry_run_001"
                    ),
                    "mutation_preflight_check_status": (
                        "blocked_by_serializer_preflight"
                    ),
                    "retry_replay_check_id": (
                        "asr_card_lifecycle_retry_replay_preflight_"
                        "llm_schema_result_card_dry_run_001"
                    ),
                    "retry_replay_check_status": "no_existing_append",
                    "retry_replay_resolution_status": "no_existing_append",
                    "serializer_result_id": (
                        "asr_card_lifecycle_append_event_serializer_"
                        "llm_schema_result_card_dry_run_001"
                    ),
                    "serialization_status": "blocked_by_preflight",
                    "event_type": "llm_schema_result",
                    "future_event_id": "llm_schema_result:card_dry_run_001",
                    "serialized_event_id": "llm_schema_result:card_dry_run_001",
                    "preview_event_id": "preview:llm_schema_result:card_dry_run_001",
                    "idempotency_key": (
                        "live_asr_card_lifecycle_append:"
                        "local_asr_idempotency_store_commit_blocker_review:"
                        "asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001:"
                        "llm_schema_result:card_dry_run_001"
                    ),
                    "transaction_idempotency_key": (
                        "live_asr_card_lifecycle_append_transaction_run:disabled:"
                        "local_asr_idempotency_store_commit_blocker_review:"
                        "asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001:"
                        "llm_schema_result:card_dry_run_001"
                    ),
                    "would_append_sequence": 31,
                    "would_append_after_sequence": 30,
                    "append_status": "blocked_existing_event",
                    "conflict_status": "existing_event_id",
                    "repository_transaction_status": "not_started",
                    "repository_transaction_commit_status": "not_committed",
                    "repository_transaction_rollback_status": "not_started",
                    "event_append_status": "not_appended",
                    "audit_event_append_status": "not_appended",
                    "idempotency_store_status": "not_written",
                    "idempotency_store_write_status": "not_written",
                    "safe_to_begin_transaction": False,
                    "safe_to_commit_transaction": False,
                    "safe_to_rollback_transaction": False,
                    "safe_to_append_event": False,
                    "safe_to_write_idempotency_store": False,
                    "safe_to_write_audit_event": False,
                    "safe_to_create_card": False,
                }
            ],
            "idempotency_store_status": "not_written",
            "idempotency_store_write_status": "not_written",
            "repository_transaction_status": "not_started",
            "repository_transaction_commit_status": "not_committed",
            "repository_transaction_rollback_status": "not_started",
            "event_append_status": "not_appended",
            "audit_event_append_status": "not_appended",
            "safe_to_write_idempotency_store": False,
            "safe_to_commit_transaction": False,
            "safe_to_append_events": False,
        }

    monkeypatch.setattr(
        app_module,
        "_llm_card_lifecycle_append_transaction_commit_preflight_from_record",
        fake_transaction_commit_preflight,
    )
    client = TestClient(create_app())
    session_id = "local_asr_idempotency_store_commit_blocker_review"
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(session_id=session_id),
    )

    response = client.post(
        (
            f"/live/asr/sessions/{session_id}"
            "/llm-card-lifecycle-append-idempotency-store-write-preflights"
        ),
        json={
            "mode": "preflight_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    assert captured_payload["mode"] == "preflight_only"
    body = response.json()
    assert body["transaction_commit_readiness_status"] == (
        "blocked_by_mutation_preflight"
    )
    assert body["idempotency_store_write_readiness_status"] == (
        "blocked_by_transaction_commit_preflight"
    )
    check = body["idempotency_store_write_preflight_checks"][0]
    assert check["transaction_commit_readiness_status"] == (
        "blocked_by_mutation_preflight"
    )
    assert check["idempotency_store_write_readiness_status"] == (
        "blocked_by_transaction_commit_preflight"
    )
    assert check["idempotency_store_write_preflight_check_status"] == (
        "blocked_by_transaction_commit_preflight"
    )
    assert check["future_idempotency_record_status"] == "blocked"
    assert check["safe_to_write_idempotency_store"] is False


def test_asr_live_llm_card_lifecycle_append_idempotency_store_write_preflights_endpoint_analyzes_schema_invalid_silenced_events():
    client = TestClient(create_app())
    session_id = "local_asr_append_idempotency_store_write_preflight_schema_invalid_review"
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(session_id=session_id),
    )
    candidate = _valid_schema_validation_candidate_response()
    candidate["usage"] = {}
    candidate["schema_result"] = "failed"

    response = client.post(
        (
            f"/live/asr/sessions/{session_id}"
            "/llm-card-lifecycle-append-idempotency-store-write-preflights"
        ),
        json={
            "mode": "preflight_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": candidate,
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["idempotency_store_write_readiness_status"] == "blocked_until_enabled"
    assert [check["event_type"] for check in body["idempotency_store_write_preflight_checks"]] == [
        "llm_schema_result",
        "suggestion_silenced",
    ]
    assert body["idempotency_store_write_preflight_checks"][1]["future_event_id"] == (
        "suggestion_silenced:card_dry_run_001"
    )
    assert body["safe_to_write_idempotency_store"] is False


def test_asr_live_llm_card_lifecycle_append_idempotency_store_write_preflights_endpoint_analyzes_policy_blocked_silenced_events():
    client = TestClient(create_app())
    session_id = "local_asr_append_idempotency_store_write_preflight_policy_blocked_review"
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id=session_id),
    )

    response = client.post(
        (
            f"/live/asr/sessions/{session_id}"
            "/llm-card-lifecycle-append-idempotency-store-write-preflights"
        ),
        json={
            "mode": "preflight_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["idempotency_store_write_readiness_status"] == "blocked_until_enabled"
    assert [check["event_type"] for check in body["idempotency_store_write_preflight_checks"]] == [
        "llm_schema_result",
        "suggestion_silenced",
    ]
    assert body["idempotency_store_write_preflight_checks"][1]["future_event_id"] == (
        "suggestion_silenced:card_dry_run_001"
    )
    assert body["safe_to_write_idempotency_store"] is False


def test_asr_live_llm_card_lifecycle_append_idempotency_store_write_preflights_endpoint_returns_404_for_unknown_request_id():
    client = TestClient(create_app())
    session_id = "local_asr_append_idempotency_store_write_preflight_unknown_request_review"
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(session_id=session_id),
    )

    response = client.post(
        (
            f"/live/asr/sessions/{session_id}"
            "/llm-card-lifecycle-append-idempotency-store-write-preflights"
        ),
        json={
            "mode": "preflight_only",
            "request_id": "missing_request_id",
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 404
    assert (
        "LLM request draft not found for card lifecycle preview dry-run: missing_request_id"
        in response.text
    )


def test_asr_live_llm_card_lifecycle_append_idempotency_store_write_preflights_endpoint_reads_persisted_record_across_app_instances(
    tmp_path,
):
    session_id = "persisted_asr_append_idempotency_store_write_preflight_review"
    first_client = TestClient(create_app(data_dir=tmp_path))
    create_response = first_client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(session_id=session_id),
    )

    second_client = TestClient(create_app(data_dir=tmp_path))
    record_path = tmp_path / "live_asr_sessions" / f"{session_id}.json"
    record_before = record_path.read_bytes()
    response = second_client.post(
        (
            f"/live/asr/sessions/{session_id}"
            "/llm-card-lifecycle-append-idempotency-store-write-preflights"
        ),
        json={
            "mode": "preflight_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    assert record_path.read_bytes() == record_before
    body = response.json()
    assert body["session_id"] == session_id
    assert body["idempotency_store_write_preflight_status"] == "analyzed"
    assert body["idempotency_store_write_preflight_checks"][0]["future_idempotency_record_key"] == (
        _card_lifecycle_append_idempotency_key(session_id, "llm_schema_result")
    )


def test_asr_live_llm_card_lifecycle_append_idempotency_store_write_preflights_endpoint_returns_404_for_missing_session():
    client = TestClient(create_app())

    response = client.post(
        (
            "/live/asr/sessions/missing_asr_append_idempotency_store_write_preflight_review"
            "/llm-card-lifecycle-append-idempotency-store-write-preflights"
        ),
        json={
            "mode": "preflight_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert response.status_code == 404
    assert (
        "ASR live session not found: missing_asr_append_idempotency_store_write_preflight_review"
        in response.text
    )


def test_asr_live_llm_card_lifecycle_append_idempotency_store_write_preflights_endpoint_rejects_request_shape_errors(
    monkeypatch,
    tmp_path,
):
    forbidden_values = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "append_idempotency_store_write_preflight_shape",
    )
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(
            session_id="local_asr_append_idempotency_store_write_preflight_shape_review"
        ),
    )
    path = (
        "/live/asr/sessions/local_asr_append_idempotency_store_write_preflight_shape_review"
        "/llm-card-lifecycle-append-idempotency-store-write-preflights"
    )

    cases = [
        ([], "request body must be an object"),
        ({}, "missing mode"),
        (
            {
                "mode": 123,
                "request_id": "request",
                "candidate_response": {},
            },
            "mode must be a string",
        ),
        (
            {
                "mode": "preflight_only",
                "request_id": 123,
                "candidate_response": {},
            },
            "request_id must be a string",
        ),
        (
            {
                "mode": "enabled",
                "request_id": "request",
                "candidate_response": {},
            },
            (
                "unsupported card lifecycle append idempotency store write "
                "preflight mode: enabled"
            ),
        ),
        (
            {
                "mode": " preflight_only ",
                "request_id": "request",
                "candidate_response": {},
            },
            (
                "unsupported card lifecycle append idempotency store write "
                "preflight mode:  preflight_only "
            ),
        ),
        (
            {
                "mode": "",
                "request_id": "request",
                "candidate_response": {},
            },
            "missing mode",
        ),
        (
            {
                "mode": "preflight_only",
                "candidate_response": {},
            },
            "missing request_id",
        ),
        (
            {
                "mode": "preflight_only",
                "request_id": " ",
                "candidate_response": {},
            },
            "missing request_id",
        ),
        (
            {
                "mode": "preflight_only",
                "request_id": "request",
            },
            "missing candidate_response",
        ),
        (
            {
                "mode": "preflight_only",
                "request_id": "request",
                "candidate_response": [],
            },
            "candidate_response must be an object",
        ),
        (
            {
                "mode": "preflight_only",
                "request_id": "request",
                "candidate_response": {},
                "idempotency_store_path": "ignored-test-value",
            },
            "extra fields are not permitted: idempotency_store_path",
        ),
    ]
    assert create_response.status_code == 201
    for payload, expected_detail in cases:
        response = client.post(path, json=payload)
        assert response.status_code == 422
        assert expected_detail in response.text or expected_detail in response.json()["detail"]
        for forbidden in forbidden_values:
            assert forbidden not in response.text


def test_asr_live_llm_card_lifecycle_append_result_audit_event_persistence_preflights_endpoint_blocks_fresh_append_until_enabled_without_mutating_events(
    monkeypatch,
    tmp_path,
):
    forbidden_values = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "append_result_audit_event_persistence_preflight_fresh",
    )
    client = TestClient(create_app())
    session_id = "local_asr_append_result_audit_event_persistence_preflight_fresh_review"
    request_id = (
        "asr_llm_request_draft_"
        "asr_suggestion_candidate_asr_state_event_asr_seg_001"
    )
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(session_id=session_id),
    )
    events_before_response = client.get(f"/live/asr/sessions/{session_id}/events")

    response = client.post(
        (
            f"/live/asr/sessions/{session_id}"
            "/llm-card-lifecycle-append-result-audit-event-persistence-preflights"
        ),
        json={
            "mode": "preflight_only",
            "request_id": request_id,
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )
    events_after_response = client.get(f"/live/asr/sessions/{session_id}/events")

    assert create_response.status_code == 201
    assert events_before_response.status_code == 200
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == session_id
    assert body["append_result_audit_event_count"] == 2
    assert body["idempotency_store_write_preflight_status"] == "analyzed"
    assert body["idempotency_store_write_readiness_status"] == "blocked_until_enabled"
    assert body["append_result_audit_event_persistence_preflight_mode"] == (
        "preflight_only"
    )
    assert body["append_result_audit_event_persistence_preflight_status"] == (
        "analyzed"
    )
    assert body["append_result_audit_event_persistence_readiness_status"] == (
        "blocked_until_enabled"
    )
    assert body[
        "append_result_audit_event_persistence_preflight_check_count"
    ] == 2
    assert body["audit_event_append_status"] == "not_appended"
    assert body["event_append_status"] == "not_appended"
    assert body["idempotency_store_status"] == "not_written"
    assert body["idempotency_store_write_status"] == "not_written"
    assert body["repository_transaction_status"] == "not_started"
    assert body["repository_transaction_commit_status"] == "not_committed"
    assert body["repository_transaction_rollback_status"] == "not_started"
    assert body["safe_to_persist_append_result_audit_event"] is False
    assert body["safe_to_write_audit_events"] is False
    assert body["safe_to_write_idempotency_store"] is False
    assert body["safe_to_begin_transaction"] is False
    assert body["safe_to_commit_transaction"] is False
    assert body["safe_to_rollback_transaction"] is False
    assert body["safe_to_mutate_events"] is False
    assert body["safe_to_append_events"] is False
    assert body["safe_to_create_card"] is False

    audit_events_by_future_event_id = {
        event["future_event_id"]: event
        for event in body["append_result_audit_events"]
    }
    idempotency_checks_by_future_event_id = {
        check["future_event_id"]: check
        for check in body["idempotency_store_write_preflight_checks"]
    }
    checks = body["append_result_audit_event_persistence_preflight_checks"]
    assert [check["event_type"] for check in checks] == [
        "llm_schema_result",
        "suggestion_card",
    ]
    assert [
        check["append_result_audit_event_persistence_preflight_check_id"]
        for check in checks
    ] == [
        (
            "asr_card_lifecycle_append_result_audit_event_persistence_preflight_"
            "llm_schema_result_card_dry_run_001"
        ),
        (
            "asr_card_lifecycle_append_result_audit_event_persistence_preflight_"
            "suggestion_card_card_dry_run_001"
        ),
    ]
    for check in checks:
        audit_event = audit_events_by_future_event_id[check["future_event_id"]]
        idempotency_check = idempotency_checks_by_future_event_id[
            check["future_event_id"]
        ]
        assert check[
            "append_result_audit_event_persistence_preflight_check_status"
        ] == "blocked_until_enabled"
        assert check["future_append_result_audit_event_id"] == audit_event[
            "audit_event_id"
        ]
        assert check["future_append_result_audit_event_type"] == (
            "card_lifecycle_append_result"
        )
        assert check["future_append_result_audit_event_status"] == (
            "would_persist_if_enabled"
        )
        assert check["append_result_audit_event_persistence_reason"] == (
            "fresh_append_requires_append_result_audit_event"
        )
        assert check["audit_event_id"] == audit_event["audit_event_id"]
        assert check["audit_idempotency_key"] == audit_event[
            "audit_idempotency_key"
        ]
        assert check["append_result_audit_event_status"] == "preview_only"
        assert check["audit_result_status"] == "skipped_transaction_disabled"
        assert check["transaction_run_id"] == audit_event["transaction_run_id"]
        assert check["transaction_run_status"] == audit_event[
            "transaction_run_status"
        ]
        assert check["append_run_id"] == audit_event["append_run_id"]
        assert check["repository_result_id"] == audit_event["repository_result_id"]
        assert check["repository_result_status"] == audit_event[
            "repository_result_status"
        ]
        assert check["repository_idempotency_key"] == audit_event[
            "repository_idempotency_key"
        ]
        assert check["preflight_append_status"] == audit_event[
            "preflight_append_status"
        ]
        assert check["preflight_conflict_status"] == audit_event[
            "preflight_conflict_status"
        ]
        assert check["audit_repository_transaction_status"] == audit_event[
            "repository_transaction_status"
        ]
        assert check["repository_write_status"] == audit_event[
            "repository_write_status"
        ]
        assert check["transaction_write_status"] == audit_event[
            "transaction_write_status"
        ]
        assert check["idempotency_store_write_preflight_check_id"] == (
            idempotency_check["idempotency_store_write_preflight_check_id"]
        )
        assert check["idempotency_store_write_preflight_check_status"] == (
            idempotency_check["idempotency_store_write_preflight_check_status"]
        )
        assert check["idempotency_store_write_readiness_status"] == (
            "blocked_until_enabled"
        )
        assert check["future_idempotency_record_status"] == (
            "would_write_if_enabled"
        )
        assert check["transaction_commit_readiness_status"] == (
            idempotency_check["transaction_commit_readiness_status"]
        )
        assert check["transaction_commit_preflight_check_id"] == (
            idempotency_check["transaction_commit_preflight_check_id"]
        )
        assert check["transaction_commit_preflight_check_status"] == (
            idempotency_check["transaction_commit_preflight_check_status"]
        )
        assert check["mutation_preflight_check_id"] == (
            idempotency_check["mutation_preflight_check_id"]
        )
        assert check["retry_replay_check_id"] == (
            idempotency_check["retry_replay_check_id"]
        )
        assert check["retry_replay_resolution_status"] == "no_existing_append"
        assert check["serializer_result_id"] == idempotency_check[
            "serializer_result_id"
        ]
        assert check["serialization_status"] == idempotency_check[
            "serialization_status"
        ]
        assert check["serialized_event_id"] == idempotency_check[
            "serialized_event_id"
        ]
        assert check["preview_event_id"] == idempotency_check["preview_event_id"]
        assert check["idempotency_key"] == idempotency_check["idempotency_key"]
        assert check["transaction_idempotency_key"] == idempotency_check[
            "transaction_idempotency_key"
        ]
        assert check["would_append_sequence"] == idempotency_check[
            "would_append_sequence"
        ]
        assert check["would_append_after_sequence"] == idempotency_check[
            "would_append_after_sequence"
        ]
        assert check["append_status"] == idempotency_check["append_status"]
        assert check["conflict_status"] == idempotency_check["conflict_status"]
        assert check["repository_transaction_status"] == "not_started"
        assert check["repository_transaction_commit_status"] == "not_committed"
        assert check["repository_transaction_rollback_status"] == "not_started"
        assert check["event_append_status"] == "not_appended"
        assert check["audit_event_append_status"] == "not_appended"
        assert check["idempotency_store_status"] == "not_written"
        assert check["idempotency_store_write_status"] == "not_written"
        assert check["safe_to_persist_append_result_audit_event"] is False
        assert check["safe_to_write_audit_event"] is False
        assert check["safe_to_write_audit_events"] is False
        assert check["safe_to_append_event"] is False
        assert check["safe_to_write_idempotency_store"] is False
        assert check["safe_to_begin_transaction"] is False
        assert check["safe_to_commit_transaction"] is False
        assert check["safe_to_rollback_transaction"] is False
        assert check["safe_to_mutate_events"] is False
        assert check["safe_to_append_events"] is False
        assert check["safe_to_create_card"] is False
    assert events_after_response.status_code == 200
    assert events_before_response.json()["events"] == events_after_response.json()["events"]
    assert events_after_response.json()["events"] == create_response.json()["live_events"]
    for forbidden in forbidden_values:
        assert forbidden not in response.text
    assert "card_lifecycle_append_result" not in [
        event["event_type"] for event in events_after_response.json()["events"]
    ]


def test_asr_live_llm_card_lifecycle_append_result_audit_event_persistence_preflights_endpoint_treats_complete_replay_as_no_persistence_replay(
    monkeypatch,
    tmp_path,
):
    forbidden_values = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "append_result_audit_event_persistence_preflight_replay",
    )
    session_id = (
        "persisted_asr_append_result_audit_event_persistence_preflight_replay_review"
    )
    first_client = TestClient(create_app(data_dir=tmp_path))
    create_response = first_client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(session_id=session_id),
    )
    record_path = tmp_path / "live_asr_sessions" / f"{session_id}.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    _append_persisted_lifecycle_event(
        record,
        session_id=session_id,
        event_type="llm_schema_result",
        sequence=999,
    )
    _append_persisted_lifecycle_event(
        record,
        session_id=session_id,
        event_type="suggestion_card",
        sequence=1000,
    )
    record_path.write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )

    second_client = TestClient(create_app(data_dir=tmp_path))
    events_before_response = second_client.get(f"/live/asr/sessions/{session_id}/events")
    record_before = record_path.read_bytes()
    response = second_client.post(
        (
            f"/live/asr/sessions/{session_id}"
            "/llm-card-lifecycle-append-result-audit-event-persistence-preflights"
        ),
        json={
            "mode": "preflight_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )
    events_after_response = second_client.get(f"/live/asr/sessions/{session_id}/events")

    assert create_response.status_code == 201
    assert events_before_response.status_code == 200
    assert response.status_code == 200
    assert events_after_response.status_code == 200
    assert record_path.read_bytes() == record_before
    body = response.json()
    assert body["idempotency_store_write_readiness_status"] == (
        "safe_replay_existing_events"
    )
    assert body["append_result_audit_event_persistence_readiness_status"] == (
        "safe_replay_existing_events"
    )
    assert body["safe_to_persist_append_result_audit_event"] is False
    assert body["safe_to_write_audit_events"] is False
    assert body["audit_event_append_status"] == "not_appended"
    checks = body["append_result_audit_event_persistence_preflight_checks"]
    assert [
        check["append_result_audit_event_persistence_preflight_check_status"]
        for check in checks
    ] == [
        "persistence_not_required_for_safe_replay",
        "persistence_not_required_for_safe_replay",
    ]
    assert [
        check["future_append_result_audit_event_status"] for check in checks
    ] == [
        "not_required_existing_replay",
        "not_required_existing_replay",
    ]
    assert {
        check["append_result_audit_event_persistence_reason"]
        for check in checks
    } == {
        "safe_replay_existing_event_requires_no_new_audit_event"
    }
    assert events_before_response.json()["events"] == events_after_response.json()["events"]
    for forbidden in forbidden_values:
        assert forbidden not in response.text


def test_asr_live_llm_card_lifecycle_append_result_audit_event_persistence_preflights_endpoint_blocks_partial_replay_without_audit_tail_write(
    monkeypatch,
    tmp_path,
):
    forbidden_values = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "append_result_audit_event_persistence_preflight_partial",
    )
    session_id = (
        "persisted_asr_append_result_audit_event_persistence_preflight_partial_review"
    )
    first_client = TestClient(create_app(data_dir=tmp_path))
    create_response = first_client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(session_id=session_id),
    )
    record_path = tmp_path / "live_asr_sessions" / f"{session_id}.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    _append_persisted_lifecycle_event(
        record,
        session_id=session_id,
        event_type="llm_schema_result",
        sequence=999,
    )
    record_path.write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )

    second_client = TestClient(create_app(data_dir=tmp_path))
    events_before_response = second_client.get(f"/live/asr/sessions/{session_id}/events")
    record_before = record_path.read_bytes()
    response = second_client.post(
        (
            f"/live/asr/sessions/{session_id}"
            "/llm-card-lifecycle-append-result-audit-event-persistence-preflights"
        ),
        json={
            "mode": "preflight_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )
    events_after_response = second_client.get(f"/live/asr/sessions/{session_id}/events")

    assert create_response.status_code == 201
    assert events_before_response.status_code == 200
    assert response.status_code == 200
    assert events_after_response.status_code == 200
    assert record_path.read_bytes() == record_before
    body = response.json()
    assert body["append_result_audit_event_persistence_readiness_status"] == (
        "blocked_by_partial_replay"
    )
    checks = body["append_result_audit_event_persistence_preflight_checks"]
    assert [
        check["append_result_audit_event_persistence_preflight_check_status"]
        for check in checks
    ] == [
        "persistence_not_required_for_safe_replay",
        "blocked_by_partial_replay",
    ]
    assert [
        check["future_append_result_audit_event_status"] for check in checks
    ] == [
        "not_required_existing_replay",
        "blocked",
    ]
    assert all(
        check["safe_to_persist_append_result_audit_event"] is False
        for check in checks
    )
    assert body["audit_event_append_status"] == "not_appended"
    assert events_before_response.json()["events"] == events_after_response.json()["events"]
    for forbidden in forbidden_values:
        assert forbidden not in response.text


def test_asr_live_llm_card_lifecycle_append_result_audit_event_persistence_preflights_endpoint_blocks_retry_replay_conflicts(
    monkeypatch,
    tmp_path,
):
    forbidden_values = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "append_result_audit_event_persistence_preflight_conflict",
    )
    session_id = (
        "persisted_asr_append_result_audit_event_persistence_preflight_conflict_review"
    )
    first_client = TestClient(create_app(data_dir=tmp_path))
    create_response = first_client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(session_id=session_id),
    )
    record_path = tmp_path / "live_asr_sessions" / f"{session_id}.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    _append_persisted_lifecycle_event(
        record,
        session_id=session_id,
        event_type="llm_schema_result",
        sequence=999,
        payload_extra={"request_id": "different_request"},
    )
    _append_persisted_lifecycle_event(
        record,
        session_id=session_id,
        event_type="suggestion_card",
        sequence=1000,
        payload_extra={"card": {"id": "different_nested_card_id"}},
    )
    record_path.write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )

    second_client = TestClient(create_app(data_dir=tmp_path))
    events_before_response = second_client.get(f"/live/asr/sessions/{session_id}/events")
    record_before = record_path.read_bytes()
    response = second_client.post(
        (
            f"/live/asr/sessions/{session_id}"
            "/llm-card-lifecycle-append-result-audit-event-persistence-preflights"
        ),
        json={
            "mode": "preflight_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )
    events_after_response = second_client.get(f"/live/asr/sessions/{session_id}/events")

    assert create_response.status_code == 201
    assert events_before_response.status_code == 200
    assert response.status_code == 200
    assert events_after_response.status_code == 200
    assert record_path.read_bytes() == record_before
    body = response.json()
    assert body["append_result_audit_event_persistence_readiness_status"] == (
        "blocked_by_retry_replay_conflict"
    )
    checks = body["append_result_audit_event_persistence_preflight_checks"]
    assert {
        check["append_result_audit_event_persistence_preflight_check_status"]
        for check in checks
    } == {"blocked_by_retry_replay_conflict"}
    assert {check["future_append_result_audit_event_status"] for check in checks} == {
        "blocked"
    }
    assert body["safe_to_persist_append_result_audit_event"] is False
    assert body["audit_event_append_status"] == "not_appended"
    assert events_before_response.json()["events"] == events_after_response.json()["events"]
    for forbidden in forbidden_values:
        assert forbidden not in response.text


def test_asr_live_llm_card_lifecycle_append_result_audit_event_persistence_preflights_endpoint_preserves_idempotency_readiness_source_when_store_preflight_blocks(
    monkeypatch,
    tmp_path,
):
    forbidden_values = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "append_result_audit_event_persistence_preflight_store_blocker",
    )
    captured_payload = {}

    def fake_idempotency_store_write_preflight(record, payload):
        captured_payload.update(payload)
        return {
            "session_id": str(record["session_id"]),
            "source": str(record["source"]),
            "trace_kind": str(record["trace_kind"]),
            "append_result_audit_event_count": 1,
            "append_result_audit_events": [
                {
                    "audit_event_id": (
                        "asr_card_lifecycle_append_result_audit_preview_"
                        "llm_schema_result_card_dry_run_001"
                    ),
                    "audit_event_type": "card_lifecycle_append_result",
                    "audit_event_status": "preview_only",
                    "audit_result_status": "skipped_transaction_disabled",
                    "audit_idempotency_key": (
                        "live_asr_card_lifecycle_append_result_audit_preview:"
                        "local_asr_audit_persistence_store_blocker_review:"
                        "asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001:"
                        "llm_schema_result:card_dry_run_001"
                    ),
                    "event_type": "llm_schema_result",
                    "future_event_id": "llm_schema_result:card_dry_run_001",
                }
            ],
            "idempotency_store_write_preflight_status": "analyzed",
            "idempotency_store_write_readiness_status": (
                "blocked_by_transaction_commit_preflight"
            ),
            "idempotency_store_write_preflight_check_count": 1,
            "idempotency_store_write_preflight_checks": [
                {
                    "idempotency_store_write_preflight_check_id": (
                        "asr_card_lifecycle_append_idempotency_store_write_preflight_"
                        "llm_schema_result_card_dry_run_001"
                    ),
                    "idempotency_store_write_preflight_check_status": (
                        "blocked_by_transaction_commit_preflight"
                    ),
                    "future_idempotency_record_status": "blocked",
                    "transaction_commit_readiness_status": (
                        "blocked_by_mutation_preflight"
                    ),
                    "transaction_commit_preflight_check_id": (
                        "asr_card_lifecycle_append_transaction_commit_preflight_"
                        "llm_schema_result_card_dry_run_001"
                    ),
                    "transaction_commit_preflight_check_status": (
                        "blocked_by_mutation_preflight"
                    ),
                    "mutation_preflight_check_id": (
                        "asr_card_lifecycle_append_mutation_preflight_"
                        "llm_schema_result_card_dry_run_001"
                    ),
                    "mutation_preflight_check_status": (
                        "blocked_by_serializer_preflight"
                    ),
                    "retry_replay_check_id": (
                        "asr_card_lifecycle_retry_replay_preflight_"
                        "llm_schema_result_card_dry_run_001"
                    ),
                    "retry_replay_check_status": "no_existing_append",
                    "retry_replay_resolution_status": "no_existing_append",
                    "serializer_result_id": (
                        "asr_card_lifecycle_append_event_serializer_"
                        "llm_schema_result_card_dry_run_001"
                    ),
                    "serialization_status": "blocked_by_preflight",
                    "event_type": "llm_schema_result",
                    "future_event_id": "llm_schema_result:card_dry_run_001",
                    "serialized_event_id": "llm_schema_result:card_dry_run_001",
                    "preview_event_id": "preview:llm_schema_result:card_dry_run_001",
                    "idempotency_key": (
                        "live_asr_card_lifecycle_append:"
                        "local_asr_audit_persistence_store_blocker_review:"
                        "asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001:"
                        "llm_schema_result:card_dry_run_001"
                    ),
                    "transaction_idempotency_key": (
                        "live_asr_card_lifecycle_append_transaction_run:disabled:"
                        "local_asr_audit_persistence_store_blocker_review:"
                        "asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001:"
                        "llm_schema_result:card_dry_run_001"
                    ),
                    "would_append_sequence": 31,
                    "would_append_after_sequence": 30,
                    "append_status": "blocked_existing_event",
                    "conflict_status": "existing_event_id",
                    "repository_transaction_status": "not_started",
                    "repository_transaction_commit_status": "not_committed",
                    "repository_transaction_rollback_status": "not_started",
                    "event_append_status": "not_appended",
                    "audit_event_append_status": "not_appended",
                    "idempotency_store_status": "not_written",
                    "idempotency_store_write_status": "not_written",
                    "safe_to_write_idempotency_store": False,
                    "safe_to_begin_transaction": False,
                    "safe_to_commit_transaction": False,
                    "safe_to_rollback_transaction": False,
                    "safe_to_append_event": False,
                    "safe_to_write_audit_event": False,
                    "safe_to_create_card": False,
                }
            ],
            "audit_event_append_status": "not_appended",
            "event_append_status": "not_appended",
            "idempotency_store_status": "not_written",
            "idempotency_store_write_status": "not_written",
            "repository_transaction_status": "not_started",
            "repository_transaction_commit_status": "not_committed",
            "repository_transaction_rollback_status": "not_started",
            "safe_to_write_idempotency_store": False,
            "safe_to_write_audit_events": False,
            "safe_to_commit_transaction": False,
            "safe_to_append_events": False,
        }

    monkeypatch.setattr(
        app_module,
        "_llm_card_lifecycle_append_idempotency_store_write_preflight_from_record",
        fake_idempotency_store_write_preflight,
    )
    client = TestClient(create_app())
    session_id = "local_asr_audit_persistence_store_blocker_review"
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(session_id=session_id),
    )
    events_before_response = client.get(f"/live/asr/sessions/{session_id}/events")

    response = client.post(
        (
            f"/live/asr/sessions/{session_id}"
            "/llm-card-lifecycle-append-result-audit-event-persistence-preflights"
        ),
        json={
            "mode": "preflight_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )
    events_after_response = client.get(f"/live/asr/sessions/{session_id}/events")

    assert create_response.status_code == 201
    assert events_before_response.status_code == 200
    assert response.status_code == 200
    assert events_after_response.status_code == 200
    assert captured_payload["mode"] == "preflight_only"
    body = response.json()
    assert body["idempotency_store_write_readiness_status"] == (
        "blocked_by_transaction_commit_preflight"
    )
    assert body["append_result_audit_event_persistence_readiness_status"] == (
        "blocked_by_idempotency_store_write_preflight"
    )
    check = body["append_result_audit_event_persistence_preflight_checks"][0]
    assert check["idempotency_store_write_readiness_status"] == (
        "blocked_by_transaction_commit_preflight"
    )
    assert check["transaction_commit_readiness_status"] == (
        "blocked_by_mutation_preflight"
    )
    assert check[
        "append_result_audit_event_persistence_preflight_check_status"
    ] == "blocked_by_idempotency_store_write_preflight"
    assert check["future_append_result_audit_event_status"] == "blocked"
    assert check["safe_to_persist_append_result_audit_event"] is False
    assert events_before_response.json()["events"] == events_after_response.json()["events"]
    for forbidden in forbidden_values:
        assert forbidden not in response.text


def test_asr_live_llm_card_lifecycle_append_result_audit_event_persistence_preflights_endpoint_analyzes_schema_invalid_silenced_events(
    monkeypatch,
    tmp_path,
):
    forbidden_values = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "append_result_audit_event_persistence_preflight_schema_invalid",
    )
    client = TestClient(create_app())
    session_id = (
        "local_asr_append_result_audit_event_persistence_preflight_schema_invalid_review"
    )
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(session_id=session_id),
    )
    events_before_response = client.get(f"/live/asr/sessions/{session_id}/events")
    candidate = _valid_schema_validation_candidate_response()
    candidate["usage"] = {}
    candidate["schema_result"] = "failed"

    response = client.post(
        (
            f"/live/asr/sessions/{session_id}"
            "/llm-card-lifecycle-append-result-audit-event-persistence-preflights"
        ),
        json={
            "mode": "preflight_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": candidate,
        },
    )
    events_after_response = client.get(f"/live/asr/sessions/{session_id}/events")

    assert create_response.status_code == 201
    assert events_before_response.status_code == 200
    assert response.status_code == 200
    assert events_after_response.status_code == 200
    body = response.json()
    assert body["append_result_audit_event_persistence_readiness_status"] == (
        "blocked_until_enabled"
    )
    assert [
        check["event_type"]
        for check in body["append_result_audit_event_persistence_preflight_checks"]
    ] == [
        "llm_schema_result",
        "suggestion_silenced",
    ]
    assert body[
        "append_result_audit_event_persistence_preflight_checks"
    ][1]["future_event_id"] == "suggestion_silenced:card_dry_run_001"
    assert body["safe_to_persist_append_result_audit_event"] is False
    assert events_before_response.json()["events"] == events_after_response.json()["events"]
    for forbidden in forbidden_values:
        assert forbidden not in response.text


def test_asr_live_llm_card_lifecycle_append_result_audit_event_persistence_preflights_endpoint_analyzes_policy_blocked_silenced_events(
    monkeypatch,
    tmp_path,
):
    forbidden_values = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "append_result_audit_event_persistence_preflight_policy_blocked",
    )
    client = TestClient(create_app())
    session_id = (
        "local_asr_append_result_audit_event_persistence_preflight_policy_blocked_review"
    )
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id=session_id),
    )
    events_before_response = client.get(f"/live/asr/sessions/{session_id}/events")

    response = client.post(
        (
            f"/live/asr/sessions/{session_id}"
            "/llm-card-lifecycle-append-result-audit-event-persistence-preflights"
        ),
        json={
            "mode": "preflight_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )
    events_after_response = client.get(f"/live/asr/sessions/{session_id}/events")

    assert create_response.status_code == 201
    assert events_before_response.status_code == 200
    assert response.status_code == 200
    assert events_after_response.status_code == 200
    body = response.json()
    assert body["append_result_audit_event_persistence_readiness_status"] == (
        "blocked_until_enabled"
    )
    assert [
        check["event_type"]
        for check in body["append_result_audit_event_persistence_preflight_checks"]
    ] == [
        "llm_schema_result",
        "suggestion_silenced",
    ]
    assert body[
        "append_result_audit_event_persistence_preflight_checks"
    ][1]["future_event_id"] == "suggestion_silenced:card_dry_run_001"
    assert body["safe_to_persist_append_result_audit_event"] is False
    assert events_before_response.json()["events"] == events_after_response.json()["events"]
    for forbidden in forbidden_values:
        assert forbidden not in response.text


def test_asr_live_llm_card_lifecycle_append_result_audit_event_persistence_preflights_endpoint_returns_404_for_unknown_request_id(
    monkeypatch,
    tmp_path,
):
    forbidden_values = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "append_result_audit_event_persistence_preflight_unknown_request",
    )
    client = TestClient(create_app())
    session_id = (
        "local_asr_append_result_audit_event_persistence_preflight_unknown_request_review"
    )
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(session_id=session_id),
    )
    events_before_response = client.get(f"/live/asr/sessions/{session_id}/events")

    response = client.post(
        (
            f"/live/asr/sessions/{session_id}"
            "/llm-card-lifecycle-append-result-audit-event-persistence-preflights"
        ),
        json={
            "mode": "preflight_only",
            "request_id": "missing_request_id",
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )
    events_after_response = client.get(f"/live/asr/sessions/{session_id}/events")

    assert create_response.status_code == 201
    assert events_before_response.status_code == 200
    assert response.status_code == 404
    assert events_after_response.status_code == 200
    assert (
        "LLM request draft not found for card lifecycle preview dry-run: missing_request_id"
        in response.text
    )
    assert events_before_response.json()["events"] == events_after_response.json()["events"]
    for forbidden in forbidden_values:
        assert forbidden not in response.text


def test_asr_live_llm_card_lifecycle_append_result_audit_event_persistence_preflights_endpoint_reads_persisted_record_across_app_instances(
    monkeypatch,
    tmp_path,
):
    forbidden_values = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "append_result_audit_event_persistence_preflight_persisted",
    )
    session_id = "persisted_asr_append_result_audit_event_persistence_preflight_review"
    first_client = TestClient(create_app(data_dir=tmp_path))
    create_response = first_client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(session_id=session_id),
    )

    second_client = TestClient(create_app(data_dir=tmp_path))
    record_path = tmp_path / "live_asr_sessions" / f"{session_id}.json"
    events_before_response = second_client.get(f"/live/asr/sessions/{session_id}/events")
    record_before = record_path.read_bytes()
    response = second_client.post(
        (
            f"/live/asr/sessions/{session_id}"
            "/llm-card-lifecycle-append-result-audit-event-persistence-preflights"
        ),
        json={
            "mode": "preflight_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )
    events_after_response = second_client.get(f"/live/asr/sessions/{session_id}/events")

    assert create_response.status_code == 201
    assert events_before_response.status_code == 200
    assert response.status_code == 200
    assert events_after_response.status_code == 200
    assert record_path.read_bytes() == record_before
    body = response.json()
    assert body["session_id"] == session_id
    assert body["append_result_audit_event_persistence_preflight_status"] == (
        "analyzed"
    )
    assert body[
        "append_result_audit_event_persistence_preflight_checks"
    ][0]["audit_idempotency_key"] == (
        "live_asr_card_lifecycle_append_result_audit_preview:"
        f"{session_id}:"
        "asr_llm_request_draft_asr_suggestion_candidate_asr_state_event_asr_seg_001:"
        "llm_schema_result:"
        "card_dry_run_001"
    )
    assert events_before_response.json()["events"] == events_after_response.json()["events"]
    for forbidden in forbidden_values:
        assert forbidden not in response.text


def test_asr_live_llm_card_lifecycle_append_result_audit_event_persistence_preflights_endpoint_returns_404_for_missing_session(
    monkeypatch,
    tmp_path,
):
    forbidden_values = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "append_result_audit_event_persistence_preflight_missing_session",
    )
    client = TestClient(create_app())

    response = client.post(
        (
            "/live/asr/sessions/missing_asr_append_result_audit_event_persistence_preflight_review"
            "/llm-card-lifecycle-append-result-audit-event-persistence-preflights"
        ),
        json={
            "mode": "preflight_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert response.status_code == 404
    assert (
        "ASR live session not found: missing_asr_append_result_audit_event_persistence_preflight_review"
        in response.text
    )
    for forbidden in forbidden_values:
        assert forbidden not in response.text


def test_asr_live_llm_card_lifecycle_append_result_audit_event_persistence_preflights_endpoint_rejects_request_shape_errors(
    monkeypatch,
    tmp_path,
):
    forbidden_values = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "append_result_audit_event_persistence_preflight_shape",
    )
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(
            session_id=(
                "local_asr_append_result_audit_event_persistence_preflight_shape_review"
            )
        ),
    )
    path = (
        "/live/asr/sessions/"
        "local_asr_append_result_audit_event_persistence_preflight_shape_review"
        "/llm-card-lifecycle-append-result-audit-event-persistence-preflights"
    )

    cases = [
        ([], "request body must be an object"),
        ({}, "missing mode"),
        (
            {
                "mode": 123,
                "request_id": "request",
                "candidate_response": {},
            },
            "mode must be a string",
        ),
        (
            {
                "mode": "preflight_only",
                "request_id": 123,
                "candidate_response": {},
            },
            "request_id must be a string",
        ),
        (
            {
                "mode": "enabled",
                "request_id": "request",
                "candidate_response": {},
            },
            (
                "unsupported card lifecycle append result audit event "
                "persistence preflight mode: enabled"
            ),
        ),
        (
            {
                "mode": " preflight_only ",
                "request_id": "request",
                "candidate_response": {},
            },
            (
                "unsupported card lifecycle append result audit event "
                "persistence preflight mode:  preflight_only "
            ),
        ),
        (
            {
                "mode": "",
                "request_id": "request",
                "candidate_response": {},
            },
            "missing mode",
        ),
        (
            {
                "mode": "preflight_only",
                "candidate_response": {},
            },
            "missing request_id",
        ),
        (
            {
                "mode": "preflight_only",
                "request_id": " ",
                "candidate_response": {},
            },
            "missing request_id",
        ),
        (
            {
                "mode": "preflight_only",
                "request_id": "request",
            },
            "missing candidate_response",
        ),
        (
            {
                "mode": "preflight_only",
                "request_id": "request",
                "candidate_response": [],
            },
            "candidate_response must be an object",
        ),
        (
            {
                "mode": "preflight_only",
                "request_id": "request",
                "candidate_response": {},
                "audit_event_repository": "ignored-test-value",
            },
            "extra fields are not permitted: audit_event_repository",
        ),
    ]
    assert create_response.status_code == 201
    for payload, expected_detail in cases:
        response = client.post(path, json=payload)
        assert response.status_code == 422
        assert expected_detail in response.text or expected_detail in response.json()["detail"]
        for forbidden in forbidden_values:
            assert forbidden not in response.text


def test_asr_live_llm_card_lifecycle_readiness_summary_endpoint_summarizes_fresh_append_without_mutating_events(
    monkeypatch,
    tmp_path,
):
    forbidden_values = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "card_lifecycle_readiness_summary_fresh",
    )
    client = TestClient(create_app())
    session_id = "local_asr_card_lifecycle_readiness_summary_fresh_review"
    request_id = (
        "asr_llm_request_draft_"
        "asr_suggestion_candidate_asr_state_event_asr_seg_001"
    )
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(session_id=session_id),
    )
    events_before_response = client.get(f"/live/asr/sessions/{session_id}/events")

    response = client.post(
        (
            f"/live/asr/sessions/{session_id}"
            "/llm-card-lifecycle-readiness-summaries"
        ),
        json={
            "mode": "summary_only",
            "request_id": request_id,
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )
    events_after_response = client.get(f"/live/asr/sessions/{session_id}/events")

    assert create_response.status_code == 201
    assert events_before_response.status_code == 200
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == session_id
    assert body["source"] == "live_asr_stream"
    assert body["trace_kind"] == "live_event"
    assert body["request_id"] == request_id
    assert body["card_lifecycle_readiness_summary_mode"] == "summary_only"
    assert body["card_lifecycle_readiness_summary_status"] == "summarized"
    assert body["card_lifecycle_overall_readiness_status"] == (
        "blocked_until_enabled"
    )
    assert body["source_preflight_kind"] == (
        "append_result_audit_event_persistence_preflight"
    )
    assert body["source_preflight_endpoint"] == (
        "POST /live/asr/sessions/{session_id}/"
        "llm-card-lifecycle-append-result-audit-event-persistence-preflights"
    )
    assert body["source_preflight_mode"] == "preflight_only"
    assert body["source_preflight_status"] == "analyzed"
    assert body["source_readiness_status"] == "blocked_until_enabled"
    assert body["source_check_count"] == 2
    assert body["future_lifecycle_status"] == "would_create_card"
    assert body["would_append_event_types_if_enabled"] == [
        "llm_schema_result",
        "suggestion_card",
    ]
    assert body["llm_call_status"] == "not_called"
    assert body["credentials_status"] == "not_read"
    assert body["config_source_status"] == "not_read"
    assert body["cost_status"] == "not_estimated"
    assert body["event_append_status"] == "not_appended"
    assert body["audit_event_append_status"] == "not_appended"
    assert body["idempotency_store_status"] == "not_written"
    assert body["idempotency_store_write_status"] == "not_written"
    assert body["repository_transaction_status"] == "not_started"
    assert body["repository_transaction_commit_status"] == "not_committed"
    assert body["repository_transaction_rollback_status"] == "not_started"
    assert body["card_lifecycle_safe_to_execute_llm"] is False
    assert body["card_lifecycle_safe_to_create_card"] is False
    assert body["card_lifecycle_safe_to_append_events"] is False
    assert body["card_lifecycle_safe_to_mutate_events"] is False
    assert body["card_lifecycle_safe_to_begin_transaction"] is False
    assert body["card_lifecycle_safe_to_commit_transaction"] is False
    assert body["card_lifecycle_safe_to_write_idempotency_store"] is False
    assert body["card_lifecycle_safe_to_persist_append_result_audit_event"] is False
    assert not any(key.startswith("safe_to_") for key in body)

    phases = body["card_lifecycle_summary_phases"]
    assert body["card_lifecycle_summary_phase_count"] == 12
    assert [phase["phase_id"] for phase in phases] == [
        "card_lifecycle_preview",
        "append_preflight",
        "append_disabled_run",
        "append_repository_dry_run",
        "append_transaction_disabled_run",
        "append_result_audit_preview",
        "retry_replay_preflight",
        "append_event_serializer_dry_run",
        "append_mutation_preflight",
        "append_transaction_commit_preflight",
        "append_idempotency_store_write_preflight",
        "append_result_audit_event_persistence_preflight",
    ]
    assert {phase["safe_to_write"] for phase in phases} == {False}
    phase_by_id = {phase["phase_id"]: phase for phase in phases}
    assert phase_by_id["card_lifecycle_preview"][
        "source_status_field"
    ] == "lifecycle_preview_status"
    assert phase_by_id["card_lifecycle_preview"][
        "source_status_value"
    ] == "previewed"
    assert phase_by_id["retry_replay_preflight"][
        "source_status_value"
    ] == "no_existing_append"
    assert phase_by_id["append_result_audit_event_persistence_preflight"] == {
        "phase_id": "append_result_audit_event_persistence_preflight",
        "phase_status": "blocked_until_enabled",
        "phase_mode": "preflight_only",
        "phase_kind": "preflight",
        "write_boundary_status": "preflight_only",
        "item_count": 2,
        "safe_to_write": False,
        "source_status_field": (
            "append_result_audit_event_persistence_readiness_status"
        ),
        "source_status_value": "blocked_until_enabled",
    }
    for decision in [
        "enabled_append_result_audit_event_persistence",
        "enabled_idempotency_store_write",
        "enabled_repository_transaction_commit",
        "enabled_retry_replay_resolution_policy",
        "enabled_card_lifecycle_mutation",
    ]:
        assert decision in body["card_lifecycle_next_required_decisions"]
    assert body["card_lifecycle_block_reasons"][:5] == [
        "append_result_audit_event_persistence_preflight_only",
        "append_result_audit_event_persistence_disabled",
        "idempotency_store_write_disabled",
        "repository_transaction_commit_disabled",
        "event_mutation_disabled",
    ]
    assert events_after_response.status_code == 200
    assert events_before_response.json()["events"] == events_after_response.json()["events"]
    assert "card_lifecycle_append_result" not in [
        event["event_type"] for event in events_after_response.json()["events"]
    ]
    for forbidden in forbidden_values:
        assert forbidden not in response.text


def test_asr_live_llm_card_lifecycle_readiness_summary_endpoint_treats_complete_replay_as_no_new_write_summary(
    monkeypatch,
    tmp_path,
):
    forbidden_values = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "card_lifecycle_readiness_summary_replay",
    )
    session_id = "persisted_asr_card_lifecycle_readiness_summary_replay_review"
    first_client = TestClient(create_app(data_dir=tmp_path))
    create_response = first_client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(session_id=session_id),
    )
    record_path = tmp_path / "live_asr_sessions" / f"{session_id}.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    _append_persisted_lifecycle_event(
        record,
        session_id=session_id,
        event_type="llm_schema_result",
        sequence=999,
    )
    _append_persisted_lifecycle_event(
        record,
        session_id=session_id,
        event_type="suggestion_card",
        sequence=1000,
    )
    record_path.write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )

    second_client = TestClient(create_app(data_dir=tmp_path))
    events_before_response = second_client.get(f"/live/asr/sessions/{session_id}/events")
    record_before = record_path.read_bytes()
    response = second_client.post(
        (
            f"/live/asr/sessions/{session_id}"
            "/llm-card-lifecycle-readiness-summaries"
        ),
        json={
            "mode": "summary_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )
    events_after_response = second_client.get(f"/live/asr/sessions/{session_id}/events")

    assert create_response.status_code == 201
    assert events_before_response.status_code == 200
    assert response.status_code == 200
    assert events_after_response.status_code == 200
    assert record_path.read_bytes() == record_before
    body = response.json()
    assert body["card_lifecycle_overall_readiness_status"] == (
        "safe_replay_existing_events"
    )
    assert body["source_readiness_status"] == "safe_replay_existing_events"
    assert body["card_lifecycle_block_reasons"][0] == (
        "safe_replay_existing_events_requires_no_new_writes"
    )
    assert not any(key.startswith("safe_to_") for key in body)
    assert body["card_lifecycle_safe_to_create_card"] is False
    assert body["card_lifecycle_safe_to_write_idempotency_store"] is False
    assert body["card_lifecycle_safe_to_persist_append_result_audit_event"] is False
    phase_by_id = {
        phase["phase_id"]: phase
        for phase in body["card_lifecycle_summary_phases"]
    }
    assert phase_by_id["retry_replay_preflight"][
        "source_status_value"
    ] == "safe_to_replay"
    assert phase_by_id["append_idempotency_store_write_preflight"][
        "source_status_value"
    ] == "safe_replay_existing_events"
    assert phase_by_id["append_result_audit_event_persistence_preflight"][
        "source_status_value"
    ] == "safe_replay_existing_events"
    assert events_before_response.json()["events"] == events_after_response.json()["events"]
    for forbidden in forbidden_values:
        assert forbidden not in response.text


def test_asr_live_llm_card_lifecycle_readiness_summary_endpoint_blocks_partial_replay_summary_without_writes(
    monkeypatch,
    tmp_path,
):
    forbidden_values = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "card_lifecycle_readiness_summary_partial",
    )
    session_id = "persisted_asr_card_lifecycle_readiness_summary_partial_review"
    first_client = TestClient(create_app(data_dir=tmp_path))
    create_response = first_client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(session_id=session_id),
    )
    record_path = tmp_path / "live_asr_sessions" / f"{session_id}.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    _append_persisted_lifecycle_event(
        record,
        session_id=session_id,
        event_type="llm_schema_result",
        sequence=999,
    )
    record_path.write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )

    second_client = TestClient(create_app(data_dir=tmp_path))
    events_before_response = second_client.get(f"/live/asr/sessions/{session_id}/events")
    record_before = record_path.read_bytes()
    response = second_client.post(
        (
            f"/live/asr/sessions/{session_id}"
            "/llm-card-lifecycle-readiness-summaries"
        ),
        json={
            "mode": "summary_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )
    events_after_response = second_client.get(f"/live/asr/sessions/{session_id}/events")

    assert create_response.status_code == 201
    assert events_before_response.status_code == 200
    assert response.status_code == 200
    assert events_after_response.status_code == 200
    assert record_path.read_bytes() == record_before
    body = response.json()
    assert body["card_lifecycle_overall_readiness_status"] == (
        "blocked_by_partial_replay"
    )
    assert body["card_lifecycle_block_reasons"][0] == (
        "partial_replay_blocks_card_lifecycle_summary"
    )
    phase_by_id = {
        phase["phase_id"]: phase
        for phase in body["card_lifecycle_summary_phases"]
    }
    assert phase_by_id["retry_replay_preflight"][
        "source_status_value"
    ] == "blocked_by_partial_replay"
    assert phase_by_id["append_result_audit_event_persistence_preflight"][
        "source_status_value"
    ] == "blocked_by_partial_replay"
    assert body["card_lifecycle_safe_to_append_events"] is False
    assert body["card_lifecycle_safe_to_persist_append_result_audit_event"] is False
    assert events_before_response.json()["events"] == events_after_response.json()["events"]
    for forbidden in forbidden_values:
        assert forbidden not in response.text


def test_asr_live_llm_card_lifecycle_readiness_summary_endpoint_blocks_retry_replay_conflict_summary(
    monkeypatch,
    tmp_path,
):
    forbidden_values = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "card_lifecycle_readiness_summary_conflict",
    )
    session_id = "persisted_asr_card_lifecycle_readiness_summary_conflict_review"
    first_client = TestClient(create_app(data_dir=tmp_path))
    create_response = first_client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(session_id=session_id),
    )
    record_path = tmp_path / "live_asr_sessions" / f"{session_id}.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    _append_persisted_lifecycle_event(
        record,
        session_id=session_id,
        event_type="llm_schema_result",
        sequence=999,
        payload_extra={"request_id": "different_request"},
    )
    _append_persisted_lifecycle_event(
        record,
        session_id=session_id,
        event_type="suggestion_card",
        sequence=1000,
        payload_extra={"card": {"id": "different_nested_card_id"}},
    )
    record_path.write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )

    second_client = TestClient(create_app(data_dir=tmp_path))
    events_before_response = second_client.get(f"/live/asr/sessions/{session_id}/events")
    record_before = record_path.read_bytes()
    response = second_client.post(
        (
            f"/live/asr/sessions/{session_id}"
            "/llm-card-lifecycle-readiness-summaries"
        ),
        json={
            "mode": "summary_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )
    events_after_response = second_client.get(f"/live/asr/sessions/{session_id}/events")

    assert create_response.status_code == 201
    assert events_before_response.status_code == 200
    assert response.status_code == 200
    assert events_after_response.status_code == 200
    assert record_path.read_bytes() == record_before
    body = response.json()
    assert body["card_lifecycle_overall_readiness_status"] == (
        "blocked_by_retry_replay_conflict"
    )
    assert body["card_lifecycle_block_reasons"][0] == (
        "retry_replay_conflict_blocks_card_lifecycle_summary"
    )
    phase_by_id = {
        phase["phase_id"]: phase
        for phase in body["card_lifecycle_summary_phases"]
    }
    assert phase_by_id["retry_replay_preflight"][
        "source_status_value"
    ] == "blocked_by_conflict"
    assert phase_by_id["append_result_audit_event_persistence_preflight"][
        "source_status_value"
    ] == "blocked_by_retry_replay_conflict"
    assert body["card_lifecycle_safe_to_commit_transaction"] is False
    assert body["card_lifecycle_safe_to_write_idempotency_store"] is False
    assert events_before_response.json()["events"] == events_after_response.json()["events"]
    for forbidden in forbidden_values:
        assert forbidden not in response.text


def test_asr_live_llm_card_lifecycle_readiness_summary_endpoint_preserves_upstream_source_blocker(
    monkeypatch,
    tmp_path,
):
    forbidden_values = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "card_lifecycle_readiness_summary_store_blocker",
    )
    captured_payload = {}

    def fake_audit_event_persistence_preflight(record, payload):
        captured_payload.update(payload)
        return {
            "session_id": str(record["session_id"]),
            "source": str(record["source"]),
            "trace_kind": str(record["trace_kind"]),
            "request_id": str(payload["request_id"]),
            "request_draft_event_id": "llm_request_draft:asr_state_event_asr_seg_001",
            "target_candidate_id": "asr_suggestion_candidate_asr_state_event_asr_seg_001",
            "future_lifecycle_status": "would_create_card",
            "would_append_event_types_if_enabled": [
                "llm_schema_result",
                "suggestion_card",
            ],
            "lifecycle_preview_mode": "dry_run_only",
            "lifecycle_preview_status": "previewed",
            "append_preflight_mode": "dry_run_only",
            "append_preflight_status": "blocked",
            "append_run_mode": "disabled",
            "append_run_status": "skipped",
            "repository_dry_run_mode": "dry_run_only",
            "repository_dry_run_status": "blocked_by_preflight",
            "transaction_run_mode": "disabled",
            "transaction_run_status": "skipped",
            "append_result_audit_mode": "preview_only",
            "append_result_audit_status": "previewed",
            "retry_replay_preflight_mode": "preflight_only",
            "retry_replay_resolution_status": "no_existing_append",
            "append_event_serializer_mode": "dry_run_only",
            "append_event_serialization_status": "blocked_by_preflight",
            "append_mutation_preflight_mode": "preflight_only",
            "append_mutation_readiness_status": "blocked_by_serializer_preflight",
            "append_transaction_commit_preflight_mode": "preflight_only",
            "transaction_commit_readiness_status": "blocked_by_mutation_preflight",
            "idempotency_store_write_preflight_mode": "preflight_only",
            "idempotency_store_write_readiness_status": (
                "blocked_by_transaction_commit_preflight"
            ),
            "append_result_audit_event_persistence_preflight_mode": (
                "preflight_only"
            ),
            "append_result_audit_event_persistence_preflight_status": "analyzed",
            "append_result_audit_event_persistence_readiness_status": (
                "blocked_by_idempotency_store_write_preflight"
            ),
            "lifecycle_preview_check_count": 5,
            "append_plan_count": 1,
            "append_run_count": 1,
            "repository_append_count": 1,
            "transaction_run_count": 1,
            "append_result_audit_event_count": 1,
            "retry_replay_check_count": 1,
            "append_event_count": 1,
            "mutation_preflight_check_count": 1,
            "transaction_commit_preflight_check_count": 1,
            "idempotency_store_write_preflight_check_count": 1,
            "append_result_audit_event_persistence_preflight_check_count": 1,
            "llm_call_status": "not_called",
            "credentials_status": "not_read",
            "config_source_status": "not_read",
            "cost_status": "not_estimated",
            "event_append_status": "not_appended",
            "audit_event_append_status": "not_appended",
            "idempotency_store_status": "not_written",
            "idempotency_store_write_status": "not_written",
            "repository_transaction_status": "not_started",
            "repository_transaction_commit_status": "not_committed",
            "repository_transaction_rollback_status": "not_started",
            "block_reasons": [
                "append_result_audit_event_persistence_preflight_only",
                "idempotency_store_write_preflight_blocked",
            ],
            "next_required_decisions": [
                "enabled_append_result_audit_event_persistence",
                "enabled_idempotency_store_write",
            ],
        }

    monkeypatch.setattr(
        app_module,
        "_llm_card_lifecycle_append_result_audit_event_persistence_preflight_from_record",
        fake_audit_event_persistence_preflight,
    )
    client = TestClient(create_app())
    session_id = "local_asr_card_lifecycle_readiness_summary_store_blocker_review"
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(session_id=session_id),
    )
    events_before_response = client.get(f"/live/asr/sessions/{session_id}/events")

    response = client.post(
        (
            f"/live/asr/sessions/{session_id}"
            "/llm-card-lifecycle-readiness-summaries"
        ),
        json={
            "mode": "summary_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )
    events_after_response = client.get(f"/live/asr/sessions/{session_id}/events")

    assert create_response.status_code == 201
    assert events_before_response.status_code == 200
    assert response.status_code == 200
    assert captured_payload["mode"] == "preflight_only"
    body = response.json()
    assert body["source_readiness_status"] == (
        "blocked_by_idempotency_store_write_preflight"
    )
    assert body["card_lifecycle_overall_readiness_status"] == (
        "blocked_by_idempotency_store_write_preflight"
    )
    assert body["card_lifecycle_block_reasons"][0] == (
        "idempotency_store_write_preflight_blocks_card_lifecycle_summary"
    )
    phase_by_id = {
        phase["phase_id"]: phase
        for phase in body["card_lifecycle_summary_phases"]
    }
    assert phase_by_id["append_result_audit_event_persistence_preflight"][
        "source_status_value"
    ] == "blocked_by_idempotency_store_write_preflight"
    assert phase_by_id["append_idempotency_store_write_preflight"][
        "source_status_value"
    ] == "blocked_by_transaction_commit_preflight"
    assert body["card_lifecycle_safe_to_write_idempotency_store"] is False
    assert body["card_lifecycle_safe_to_persist_append_result_audit_event"] is False
    assert events_after_response.status_code == 200
    assert events_before_response.json()["events"] == events_after_response.json()["events"]
    for forbidden in forbidden_values:
        assert forbidden not in response.text


def test_asr_live_llm_card_lifecycle_readiness_summary_endpoint_returns_404_for_unknown_request_id(
    monkeypatch,
    tmp_path,
):
    forbidden_values = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "card_lifecycle_readiness_summary_unknown_request",
    )
    client = TestClient(create_app())
    session_id = "local_asr_card_lifecycle_readiness_summary_unknown_request_review"
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(session_id=session_id),
    )
    events_before_response = client.get(f"/live/asr/sessions/{session_id}/events")

    response = client.post(
        (
            f"/live/asr/sessions/{session_id}"
            "/llm-card-lifecycle-readiness-summaries"
        ),
        json={
            "mode": "summary_only",
            "request_id": "missing_request_id",
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )
    events_after_response = client.get(f"/live/asr/sessions/{session_id}/events")

    assert create_response.status_code == 201
    assert events_before_response.status_code == 200
    assert response.status_code == 404
    assert (
        "LLM request draft not found for card lifecycle preview dry-run: missing_request_id"
        in response.text
    )
    assert events_after_response.status_code == 200
    assert events_before_response.json()["events"] == events_after_response.json()["events"]
    for forbidden in forbidden_values:
        assert forbidden not in response.text


def test_asr_live_llm_card_lifecycle_readiness_summary_endpoint_returns_404_for_missing_session(
    monkeypatch,
    tmp_path,
):
    forbidden_values = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "card_lifecycle_readiness_summary_missing_session",
    )
    client = TestClient(create_app())

    response = client.post(
        (
            "/live/asr/sessions/missing_asr_card_lifecycle_readiness_summary_review"
            "/llm-card-lifecycle-readiness-summaries"
        ),
        json={
            "mode": "summary_only",
            "request_id": (
                "asr_llm_request_draft_"
                "asr_suggestion_candidate_asr_state_event_asr_seg_001"
            ),
            "candidate_response": _valid_schema_validation_candidate_response(),
        },
    )

    assert response.status_code == 404
    assert (
        "ASR live session not found: missing_asr_card_lifecycle_readiness_summary_review"
        in response.text
    )
    for forbidden in forbidden_values:
        assert forbidden not in response.text


def test_asr_live_llm_card_lifecycle_readiness_summary_endpoint_rejects_request_shape_errors(
    monkeypatch,
    tmp_path,
):
    forbidden_values = _install_no_llm_config_or_secret_read_guards(
        monkeypatch,
        tmp_path,
        "card_lifecycle_readiness_summary_shape",
    )
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload_without_revision(
            session_id="local_asr_card_lifecycle_readiness_summary_shape_review"
        ),
    )
    path = (
        "/live/asr/sessions/local_asr_card_lifecycle_readiness_summary_shape_review"
        "/llm-card-lifecycle-readiness-summaries"
    )
    cases = [
        ([], "request body must be an object"),
        ({}, "missing mode"),
        (
            {
                "mode": 123,
                "request_id": "request",
                "candidate_response": {},
            },
            "mode must be a string",
        ),
        (
            {
                "mode": "summary_only",
                "request_id": 123,
                "candidate_response": {},
            },
            "request_id must be a string",
        ),
        (
            {
                "mode": "preflight_only",
                "request_id": "request",
                "candidate_response": {},
            },
            "unsupported card lifecycle readiness summary mode: preflight_only",
        ),
        (
            {
                "mode": " summary_only ",
                "request_id": "request",
                "candidate_response": {},
            },
            "unsupported card lifecycle readiness summary mode:  summary_only ",
        ),
        (
            {
                "mode": "",
                "request_id": "request",
                "candidate_response": {},
            },
            "missing mode",
        ),
        (
            {
                "mode": "summary_only",
                "candidate_response": {},
            },
            "missing request_id",
        ),
        (
            {
                "mode": "summary_only",
                "request_id": " ",
                "candidate_response": {},
            },
            "missing request_id",
        ),
        (
            {
                "mode": "summary_only",
                "request_id": "request",
            },
            "missing candidate_response",
        ),
        (
            {
                "mode": "summary_only",
                "request_id": "request",
                "candidate_response": [],
            },
            "candidate_response must be an object",
        ),
        (
            {
                "mode": "summary_only",
                "request_id": "request",
                "candidate_response": {},
                "phase_filter": "ignored-test-value",
            },
            "extra fields are not permitted: phase_filter",
        ),
    ]
    assert create_response.status_code == 201
    for payload, expected_detail in cases:
        response = client.post(path, json=payload)
        assert response.status_code == 422
        assert expected_detail in response.text or expected_detail in response.json()["detail"]
        for forbidden in forbidden_values:
            assert forbidden not in response.text


def test_asr_live_llm_provider_readiness_endpoint_reports_not_ready_without_reading_config(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "llm-gateway.local.json"
    config_path.write_text(
        '{"base_url":"https://config-read-sentinel.invalid","api_key":"TEST_SENTINEL_VALUE","model":"readiness-sentinel-model"}',
        encoding="utf-8",
    )
    monkeypatch.setenv("MEETING_COPILOT_LLM_CONFIG", str(config_path))
    original_read_text = Path.read_text

    def reject_llm_config_read(path, *args, **kwargs):
        if Path(path) == config_path:
            raise AssertionError("llm provider readiness must not read config files")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", reject_llm_config_read)
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="local_asr_provider_readiness_review"),
    )
    events_before_response = client.get(
        "/live/asr/sessions/local_asr_provider_readiness_review/events"
    )

    response = client.get(
        "/live/asr/sessions/local_asr_provider_readiness_review/llm-provider-readiness"
    )
    events_after_response = client.get(
        "/live/asr/sessions/local_asr_provider_readiness_review/events"
    )

    assert create_response.status_code == 201
    assert events_before_response.status_code == 200
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "session_id": "local_asr_provider_readiness_review",
        "source": "live_asr_stream",
        "trace_kind": "live_event",
        "readiness_status": "not_ready",
        "executor_mode": "disabled",
        "enabled_mode_status": "blocked",
        "provider_protocol": "openai_compatible_chat_completions",
        "provider_config_status": "not_loaded",
        "provider_config_source": "not_read",
        "credentials_status": "not_read",
        "base_url_status": "not_configured",
        "model_status": "not_configured",
        "llm_call_status": "not_called",
        "schema_status": "not_generated",
        "card_status": "not_created",
        "cost_status": "not_estimated",
        "request_draft_count": 5,
        "execution_preview_count": 5,
        "disabled_run_count": 5,
        "queue_status": "has_request_drafts",
        "can_execute_llm": False,
        "block_reasons": [
            "llm_executor_disabled",
            "provider_config_not_loaded",
            "credentials_not_read",
            "enabled_mode_not_designed",
        ],
        "required_config_fields": ["base_url", "api_key", "model"],
        "next_required_decisions": [
            "provider_config_secret_boundary",
            "enabled_executor_mode_contract",
            "schema_validation_and_card_lifecycle",
            "token_cost_accounting",
            "timeout_retry_and_degradation_policy",
        ],
    }
    assert "config-read-sentinel.invalid" not in response.text
    assert "TEST_SENTINEL_VALUE" not in response.text
    assert "readiness-sentinel-model" not in response.text
    assert events_after_response.status_code == 200
    assert events_before_response.json()["events"] == events_after_response.json()["events"]
    assert events_after_response.json()["events"] == create_response.json()["live_events"]


def test_asr_live_llm_provider_readiness_endpoint_returns_empty_queue_for_transcript_only_session():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json={
            "session_id": "local_asr_provider_readiness_empty_review",
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
        "/live/asr/sessions/local_asr_provider_readiness_empty_review/llm-provider-readiness"
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["readiness_status"] == "not_ready"
    assert body["executor_mode"] == "disabled"
    assert body["can_execute_llm"] is False
    assert body["request_draft_count"] == 0
    assert body["execution_preview_count"] == 0
    assert body["disabled_run_count"] == 0
    assert body["queue_status"] == "empty"
    assert body["block_reasons"] == [
        "llm_executor_disabled",
        "provider_config_not_loaded",
        "credentials_not_read",
        "enabled_mode_not_designed",
        "no_request_drafts",
    ]


def test_asr_live_llm_provider_readiness_endpoint_reads_persisted_record_across_app_instances(
    tmp_path,
):
    first_client = TestClient(create_app(data_dir=tmp_path))
    create_response = first_client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="persisted_asr_provider_readiness_review"),
    )

    second_client = TestClient(create_app(data_dir=tmp_path))
    response = second_client.get(
        "/live/asr/sessions/persisted_asr_provider_readiness_review/llm-provider-readiness"
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == "persisted_asr_provider_readiness_review"
    assert body["request_draft_count"] == 5
    assert body["execution_preview_count"] == 5
    assert body["disabled_run_count"] == 5
    assert body["provider_config_source"] == "not_read"
    assert body["credentials_status"] == "not_read"


def test_asr_live_llm_provider_readiness_endpoint_returns_404_for_missing_session():
    client = TestClient(create_app())

    response = client.get(
        "/live/asr/sessions/missing_asr_review/llm-provider-readiness"
    )

    assert response.status_code == 404
    assert "ASR live session not found: missing_asr_review" in response.text


def test_asr_live_llm_provider_config_boundary_endpoint_returns_template_without_reading_config(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "llm-gateway.local.json"
    config_path.write_text(
        (
            '{"base_url":"https://boundary-read-sentinel.invalid",'
            '"api_key":"TEST_BOUNDARY_SENTINEL_VALUE",'
            '"model":"boundary-sentinel-model",'
            '"authorization":"Bearer SHOULD_NOT_APPEAR"}'
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MEETING_COPILOT_LLM_CONFIG", str(config_path))
    original_read_text = Path.read_text

    def reject_llm_config_read(path, *args, **kwargs):
        if Path(path) == config_path:
            raise AssertionError("llm provider config boundary must not read config files")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", reject_llm_config_read)
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="local_asr_provider_config_boundary_review"),
    )
    events_before_response = client.get(
        "/live/asr/sessions/local_asr_provider_config_boundary_review/events"
    )

    response = client.get(
        "/live/asr/sessions/local_asr_provider_config_boundary_review/llm-provider-config-boundary"
    )
    events_after_response = client.get(
        "/live/asr/sessions/local_asr_provider_config_boundary_review/events"
    )

    assert create_response.status_code == 201
    assert events_before_response.status_code == 200
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "session_id": "local_asr_provider_config_boundary_review",
        "source": "live_asr_stream",
        "trace_kind": "live_event",
        "boundary_status": "template_only",
        "provider_protocol": "openai_compatible_chat_completions",
        "config_load_status": "not_loaded",
        "config_source_status": "not_read",
        "credentials_status": "not_read",
        "llm_call_status": "not_called",
        "schema_status": "not_generated",
        "card_status": "not_created",
        "cost_status": "not_estimated",
        "safe_to_execute": False,
        "field_count": 5,
        "required_field_names": ["base_url", "api_key", "model"],
        "fields": [
            {
                "name": "base_url",
                "classification": "public_endpoint",
                "display_policy": "origin_only_or_masked",
                "required": True,
                "response_value_policy": "never_return_raw_value",
            },
            {
                "name": "api_key",
                "classification": "secret",
                "display_policy": "never_display",
                "required": True,
                "response_value_policy": "never_return_value",
            },
            {
                "name": "model",
                "classification": "public_model_id",
                "display_policy": "display_allowed",
                "required": True,
                "response_value_policy": (
                    "return_configured_value_only_after_loader_mask_review"
                ),
            },
            {
                "name": "timeout_seconds",
                "classification": "non_secret_runtime",
                "display_policy": "display_allowed",
                "required": False,
                "response_value_policy": "return_configured_value_after_validation",
            },
            {
                "name": "ca_bundle_path",
                "classification": "local_path_sensitive",
                "display_policy": "basename_only_or_not_displayed",
                "required": False,
                "response_value_policy": "never_return_absolute_path",
            },
        ],
        "allowed_response_fields": [
            "provider_protocol",
            "model",
            "base_url_origin",
            "timeout_seconds",
            "config_status",
        ],
        "forbidden_response_fields": [
            "api_key",
            "authorization",
            "bearer_token",
            "raw_config",
        ],
        "secret_storage_policy": "configs/local_only_or_os_keychain_future",
        "next_required_decisions": [
            "provider_config_loader_contract",
            "secret_storage_adapter",
            "masked_provider_status_response",
            "enabled_executor_mode_contract",
            "schema_validation_and_card_lifecycle",
        ],
    }
    assert "boundary-read-sentinel.invalid" not in response.text
    assert "TEST_BOUNDARY_SENTINEL_VALUE" not in response.text
    assert "boundary-sentinel-model" not in response.text
    assert "SHOULD_NOT_APPEAR" not in response.text
    assert "api_key" not in set(body)
    assert "authorization" not in set(body)
    assert "bearer_token" not in set(body)
    assert "raw_config" not in set(body)
    api_key_field = next(field for field in body["fields"] if field["name"] == "api_key")
    assert api_key_field["classification"] == "secret"
    assert api_key_field["display_policy"] == "never_display"
    assert api_key_field["response_value_policy"] == "never_return_value"
    for field in body["fields"]:
        assert "value" not in field
        assert "configured_value" not in field
        assert "raw_value" not in field
    assert events_after_response.status_code == 200
    assert events_before_response.json()["events"] == events_after_response.json()["events"]
    assert events_after_response.json()["events"] == create_response.json()["live_events"]


def test_asr_live_llm_provider_config_boundary_endpoint_returns_template_for_transcript_only_session():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json={
            "session_id": "local_asr_provider_config_boundary_empty_review",
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
        "/live/asr/sessions/local_asr_provider_config_boundary_empty_review/llm-provider-config-boundary"
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["boundary_status"] == "template_only"
    assert body["config_load_status"] == "not_loaded"
    assert body["config_source_status"] == "not_read"
    assert body["credentials_status"] == "not_read"
    assert body["llm_call_status"] == "not_called"
    assert body["safe_to_execute"] is False
    assert body["field_count"] == 5
    assert body["required_field_names"] == ["base_url", "api_key", "model"]


def test_asr_live_llm_provider_config_boundary_endpoint_reads_persisted_record_across_app_instances(
    tmp_path,
):
    first_client = TestClient(create_app(data_dir=tmp_path))
    create_response = first_client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="persisted_asr_provider_config_boundary_review"),
    )

    second_client = TestClient(create_app(data_dir=tmp_path))
    response = second_client.get(
        "/live/asr/sessions/persisted_asr_provider_config_boundary_review/llm-provider-config-boundary"
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == "persisted_asr_provider_config_boundary_review"
    assert body["boundary_status"] == "template_only"
    assert body["provider_protocol"] == "openai_compatible_chat_completions"
    assert body["config_source_status"] == "not_read"
    assert body["credentials_status"] == "not_read"
    assert body["safe_to_execute"] is False


def test_asr_live_llm_provider_config_boundary_endpoint_returns_404_for_missing_session():
    client = TestClient(create_app())

    response = client.get(
        "/live/asr/sessions/missing_asr_review/llm-provider-config-boundary"
    )

    assert response.status_code == 404
    assert "ASR live session not found: missing_asr_review" in response.text


def test_asr_live_llm_provider_masked_status_endpoint_returns_template_without_reading_config(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "llm-gateway.local.json"
    config_path.write_text(
        (
            '{"base_url":"https://masked-status-read-sentinel.invalid",'
            '"api_key":"TEST_MASKED_STATUS_SENTINEL_VALUE",'
            '"model":"masked-status-sentinel-model",'
            '"timeout_seconds":31,'
            '"ca_bundle_path":"/very/private/root-ca-sentinel.pem",'
            '"authorization":"Bearer MASKED_STATUS_SHOULD_NOT_APPEAR"}'
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MEETING_COPILOT_LLM_CONFIG", str(config_path))
    monkeypatch.setenv("OPENAI_API_KEY", "TEST_MASKED_STATUS_ENV_OPENAI_KEY")
    monkeypatch.setenv(
        "MEETING_COPILOT_LLM_API_KEY",
        "TEST_MASKED_STATUS_ENV_MEETING_KEY",
    )
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

    def is_llm_config_path(path) -> bool:
        if Path(path) == config_path:
            return True
        return False

    def reject_llm_config_read_text(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError("llm provider masked status must not read config files")
        return original_read_text(path, *args, **kwargs)

    def reject_llm_config_read_bytes(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError("llm provider masked status must not read config bytes")
        return original_read_bytes(path, *args, **kwargs)

    def reject_llm_config_path_open(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError("llm provider masked status must not open config files")
        return original_path_open(path, *args, **kwargs)

    def reject_llm_config_builtin_open(file, *args, **kwargs):
        if is_llm_config_path(file):
            raise AssertionError("llm provider masked status must not open config files")
        return original_builtin_open(file, *args, **kwargs)

    def reject_llm_secret_getenv(key, *args, **kwargs):
        if key in {"OPENAI_API_KEY", "MEETING_COPILOT_LLM_API_KEY"}:
            raise AssertionError("llm provider masked status must not read env secrets")
        return original_getenv(key, *args, **kwargs)

    def reject_llm_secret_environ_get(key, *args, **kwargs):
        if key in {"OPENAI_API_KEY", "MEETING_COPILOT_LLM_API_KEY"}:
            raise AssertionError("llm provider masked status must not read env secrets")
        return original_environ_get(key, *args, **kwargs)

    def reject_llm_gateway_config_load(*args, **kwargs):
        raise AssertionError("llm provider masked status must not load llm gateway config")

    monkeypatch.setattr(Path, "read_text", reject_llm_config_read_text)
    monkeypatch.setattr(Path, "read_bytes", reject_llm_config_read_bytes)
    monkeypatch.setattr(Path, "open", reject_llm_config_path_open)
    monkeypatch.setattr(builtins, "open", reject_llm_config_builtin_open)
    monkeypatch.setattr(os, "getenv", reject_llm_secret_getenv)
    monkeypatch.setattr(os.environ, "get", reject_llm_secret_environ_get)
    monkeypatch.setattr(
        app_module,
        "load_llm_gateway_config",
        reject_llm_gateway_config_load,
        raising=False,
    )
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="local_asr_provider_masked_status_review"),
    )
    events_before_response = client.get(
        "/live/asr/sessions/local_asr_provider_masked_status_review/events"
    )

    response = client.get(
        "/live/asr/sessions/local_asr_provider_masked_status_review/llm-provider-masked-status"
    )
    events_after_response = client.get(
        "/live/asr/sessions/local_asr_provider_masked_status_review/events"
    )

    assert create_response.status_code == 201
    assert events_before_response.status_code == 200
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "session_id": "local_asr_provider_masked_status_review",
        "source": "live_asr_stream",
        "trace_kind": "live_event",
        "status_kind": "masked_provider_status",
        "status_mode": "template_only",
        "provider_protocol": "openai_compatible_chat_completions",
        "provider_status": "not_configured",
        "config_load_status": "not_loaded",
        "config_source_status": "not_read",
        "credentials_status": "not_read",
        "llm_call_status": "not_called",
        "schema_status": "not_generated",
        "card_status": "not_created",
        "cost_status": "not_estimated",
        "safe_to_execute": False,
        "display_values": {
            "base_url_origin": None,
            "model": None,
            "timeout_seconds": None,
            "ca_bundle_name": None,
            "api_key": None,
        },
        "display_value_status": {
            "base_url_origin": "not_read",
            "model": "not_read",
            "timeout_seconds": "not_read",
            "ca_bundle_name": "not_read",
            "api_key": "never_display",
        },
        "masked_value_policy": {
            "api_key": "never_return_value_or_mask",
            "base_url": "origin_only_after_loader_review",
            "model": "display_allowed_after_loader_review",
            "timeout_seconds": "display_allowed_after_validation",
            "ca_bundle_path": "basename_only_after_loader_review",
        },
        "forbidden_status_signals": [
            "api_key_present",
            "api_key_valid",
            "api_key_length",
            "api_key_hash",
            "api_key_prefix",
            "api_key_suffix",
            "api_key_fingerprint",
            "authorization",
            "bearer_token",
            "raw_config",
        ],
        "block_reasons": [
            "template_only_status",
            "provider_config_not_loaded",
            "credentials_not_read",
            "llm_executor_disabled",
        ],
        "next_required_decisions": [
            "provider_config_loader_contract",
            "secret_storage_adapter",
            "authorized_masked_status_loader",
            "enabled_executor_mode_contract",
            "schema_validation_and_card_lifecycle",
        ],
    }
    assert "masked-status-read-sentinel.invalid" not in response.text
    assert "TEST_MASKED_STATUS_SENTINEL_VALUE" not in response.text
    assert "masked-status-sentinel-model" not in response.text
    assert "root-ca-sentinel.pem" not in response.text
    assert "MASKED_STATUS_SHOULD_NOT_APPEAR" not in response.text
    assert "TEST_MASKED_STATUS_ENV_OPENAI_KEY" not in response.text
    assert "TEST_MASKED_STATUS_ENV_MEETING_KEY" not in response.text
    assert "sk-" not in response.text
    assert "Bearer" not in response.text
    assert body["display_values"]["api_key"] is None
    assert body["display_value_status"]["api_key"] == "never_display"
    assert body["masked_value_policy"]["api_key"] == "never_return_value_or_mask"
    for forbidden_key in (
        "api_key_present",
        "api_key_valid",
        "api_key_length",
        "api_key_hash",
        "api_key_prefix",
        "api_key_suffix",
        "api_key_fingerprint",
        "authorization",
        "bearer_token",
        "raw_config",
    ):
        assert forbidden_key not in set(body)
    for display_value in body["display_values"].values():
        assert display_value is None
    assert events_after_response.status_code == 200
    assert events_before_response.json()["events"] == events_after_response.json()["events"]
    assert events_after_response.json()["events"] == create_response.json()["live_events"]


def test_asr_live_llm_provider_masked_status_endpoint_returns_template_for_transcript_only_session():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json={
            "session_id": "local_asr_provider_masked_status_empty_review",
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
        "/live/asr/sessions/local_asr_provider_masked_status_empty_review/llm-provider-masked-status"
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["status_kind"] == "masked_provider_status"
    assert body["status_mode"] == "template_only"
    assert body["provider_status"] == "not_configured"
    assert body["config_load_status"] == "not_loaded"
    assert body["config_source_status"] == "not_read"
    assert body["credentials_status"] == "not_read"
    assert body["llm_call_status"] == "not_called"
    assert body["safe_to_execute"] is False
    assert body["display_values"] == {
        "base_url_origin": None,
        "model": None,
        "timeout_seconds": None,
        "ca_bundle_name": None,
        "api_key": None,
    }
    assert body["display_value_status"] == {
        "base_url_origin": "not_read",
        "model": "not_read",
        "timeout_seconds": "not_read",
        "ca_bundle_name": "not_read",
        "api_key": "never_display",
    }
    assert body["masked_value_policy"]["api_key"] == "never_return_value_or_mask"
    assert body["forbidden_status_signals"] == [
        "api_key_present",
        "api_key_valid",
        "api_key_length",
        "api_key_hash",
        "api_key_prefix",
        "api_key_suffix",
        "api_key_fingerprint",
        "authorization",
        "bearer_token",
        "raw_config",
    ]


def test_asr_live_llm_provider_masked_status_endpoint_reads_persisted_record_across_app_instances(
    tmp_path,
):
    first_client = TestClient(create_app(data_dir=tmp_path))
    create_response = first_client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="persisted_asr_provider_masked_status_review"),
    )

    second_client = TestClient(create_app(data_dir=tmp_path))
    response = second_client.get(
        "/live/asr/sessions/persisted_asr_provider_masked_status_review/llm-provider-masked-status"
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == "persisted_asr_provider_masked_status_review"
    assert body["status_kind"] == "masked_provider_status"
    assert body["status_mode"] == "template_only"
    assert body["provider_protocol"] == "openai_compatible_chat_completions"
    assert body["config_source_status"] == "not_read"
    assert body["credentials_status"] == "not_read"
    assert body["safe_to_execute"] is False


def test_asr_live_llm_provider_masked_status_endpoint_returns_404_for_missing_session():
    client = TestClient(create_app())

    response = client.get(
        "/live/asr/sessions/missing_asr_review/llm-provider-masked-status"
    )

    assert response.status_code == 404
    assert "ASR live session not found: missing_asr_review" in response.text


def test_asr_live_llm_provider_config_validation_endpoint_validates_request_body_without_reading_config(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "llm-gateway.local.json"
    config_path.write_text(
        (
            '{"base_url":"https://provider-validation-read-sentinel.invalid",'
            '"api_key":"TEST_PROVIDER_VALIDATION_CONFIG_SECRET",'
            '"model":"provider-validation-config-model",'
            '"timeout_seconds":59,'
            '"ca_bundle_path":"/very/private/provider-validation-ca.pem",'
            '"authorization":"Bearer PROVIDER_VALIDATION_CONFIG_BEARER"}'
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MEETING_COPILOT_LLM_CONFIG", str(config_path))
    monkeypatch.setenv("OPENAI_API_KEY", "TEST_PROVIDER_VALIDATION_ENV_OPENAI_KEY")
    monkeypatch.setenv(
        "MEETING_COPILOT_LLM_API_KEY",
        "TEST_PROVIDER_VALIDATION_ENV_MEETING_KEY",
    )
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

    def is_llm_config_path(path) -> bool:
        return Path(path) == config_path

    def reject_llm_config_read_text(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError(
                "llm provider config validation must not read config files"
            )
        return original_read_text(path, *args, **kwargs)

    def reject_llm_config_read_bytes(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError(
                "llm provider config validation must not read config bytes"
            )
        return original_read_bytes(path, *args, **kwargs)

    def reject_llm_config_path_open(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError(
                "llm provider config validation must not open config files"
            )
        return original_path_open(path, *args, **kwargs)

    def reject_llm_config_builtin_open(file, *args, **kwargs):
        if is_llm_config_path(file):
            raise AssertionError(
                "llm provider config validation must not open config files"
            )
        return original_builtin_open(file, *args, **kwargs)

    def reject_llm_secret_getenv(key, *args, **kwargs):
        if key in {"OPENAI_API_KEY", "MEETING_COPILOT_LLM_API_KEY"}:
            raise AssertionError(
                "llm provider config validation must not read env secrets"
            )
        return original_getenv(key, *args, **kwargs)

    def reject_llm_secret_environ_get(key, *args, **kwargs):
        if key in {"OPENAI_API_KEY", "MEETING_COPILOT_LLM_API_KEY"}:
            raise AssertionError(
                "llm provider config validation must not read env secrets"
            )
        return original_environ_get(key, *args, **kwargs)

    def reject_llm_gateway_config_load(*args, **kwargs):
        raise AssertionError(
            "llm provider config validation must not load llm gateway config"
        )

    monkeypatch.setattr(Path, "read_text", reject_llm_config_read_text)
    monkeypatch.setattr(Path, "read_bytes", reject_llm_config_read_bytes)
    monkeypatch.setattr(Path, "open", reject_llm_config_path_open)
    monkeypatch.setattr(builtins, "open", reject_llm_config_builtin_open)
    monkeypatch.setattr(os, "getenv", reject_llm_secret_getenv)
    monkeypatch.setattr(os.environ, "get", reject_llm_secret_environ_get)
    monkeypatch.setattr(
        app_module,
        "load_llm_gateway_config",
        reject_llm_gateway_config_load,
        raising=False,
    )
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="local_asr_provider_config_validation_review"),
    )
    events_before_response = client.get(
        "/live/asr/sessions/local_asr_provider_config_validation_review/events"
    )

    response = client.post(
        "/live/asr/sessions/local_asr_provider_config_validation_review/llm-provider-config-validation",
        json=_valid_llm_provider_config_validation_payload(),
    )
    events_after_response = client.get(
        "/live/asr/sessions/local_asr_provider_config_validation_review/events"
    )

    assert create_response.status_code == 201
    assert events_before_response.status_code == 200
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "session_id": "local_asr_provider_config_validation_review",
        "source": "live_asr_stream",
        "trace_kind": "live_event",
        "validation_kind": "provider_config_request_body",
        "validation_status": "valid",
        "validation_mode": "request_body_only",
        "provider_protocol": "openai_compatible_chat_completions",
        "config_source_status": "request_body_only",
        "config_file_status": "not_read",
        "credentials_status": "provided_but_not_returned",
        "llm_call_status": "not_called",
        "schema_status": "not_generated",
        "card_status": "not_created",
        "cost_status": "not_estimated",
        "safe_to_execute": False,
        "validated_fields": [
            "provider_protocol",
            "base_url",
            "api_key",
            "model",
            "timeout_seconds",
            "ca_bundle_path",
        ],
        "display_values": {
            "base_url_origin": "https://provider-validation.example.invalid",
            "model": "gpt-5.5",
            "timeout_seconds": 30,
            "ca_bundle_name": "root-ca.pem",
            "api_key": None,
        },
        "display_value_status": {
            "base_url_origin": "derived_from_request_body",
            "model": "provided_non_secret",
            "timeout_seconds": "provided_non_secret",
            "ca_bundle_name": "basename_only",
            "api_key": "never_display",
        },
        "forbidden_response_fields": [
            "api_key",
            "authorization",
            "bearer_token",
            "raw_config",
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
        "next_required_decisions": [
            "secret_storage_adapter",
            "authorized_config_file_loader",
            "enabled_executor_mode_contract",
            "schema_validation_and_card_lifecycle",
        ],
    }
    assert "TEST_PROVIDER_VALIDATION_SECRET_VALUE" not in response.text
    assert "provider-validation-read-sentinel.invalid" not in response.text
    assert "TEST_PROVIDER_VALIDATION_CONFIG_SECRET" not in response.text
    assert "provider-validation-config-model" not in response.text
    assert "provider-validation-ca.pem" not in response.text
    assert "PROVIDER_VALIDATION_CONFIG_BEARER" not in response.text
    assert "TEST_PROVIDER_VALIDATION_ENV_OPENAI_KEY" not in response.text
    assert "TEST_PROVIDER_VALIDATION_ENV_MEETING_KEY" not in response.text
    assert "sk-" not in response.text
    assert "Bearer" not in response.text
    assert body["display_values"]["api_key"] is None
    assert body["display_value_status"]["api_key"] == "never_display"
    for forbidden_key in (
        "api_key_present",
        "api_key_valid",
        "api_key_length",
        "api_key_hash",
        "api_key_prefix",
        "api_key_suffix",
        "api_key_fingerprint",
        "authorization",
        "bearer_token",
        "raw_config",
    ):
        assert forbidden_key not in set(body)
    assert events_after_response.status_code == 200
    assert events_before_response.json()["events"] == events_after_response.json()["events"]
    assert events_after_response.json()["events"] == create_response.json()["live_events"]


def test_asr_live_llm_provider_config_validation_endpoint_accepts_transcript_only_session():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json={
            "session_id": "local_asr_provider_config_validation_empty_review",
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
    payload = _valid_llm_provider_config_validation_payload()
    del payload["timeout_seconds"]
    del payload["ca_bundle_path"]

    response = client.post(
        "/live/asr/sessions/local_asr_provider_config_validation_empty_review/llm-provider-config-validation",
        json=payload,
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["validation_status"] == "valid"
    assert body["validation_mode"] == "request_body_only"
    assert body["config_file_status"] == "not_read"
    assert body["credentials_status"] == "provided_but_not_returned"
    assert body["llm_call_status"] == "not_called"
    assert body["safe_to_execute"] is False
    assert body["display_values"] == {
        "base_url_origin": "https://provider-validation.example.invalid",
        "model": "gpt-5.5",
        "timeout_seconds": None,
        "ca_bundle_name": None,
        "api_key": None,
    }
    assert body["display_value_status"] == {
        "base_url_origin": "derived_from_request_body",
        "model": "provided_non_secret",
        "timeout_seconds": "not_provided",
        "ca_bundle_name": "not_provided",
        "api_key": "never_display",
    }
    assert "TEST_PROVIDER_VALIDATION_SECRET_VALUE" not in response.text


def test_asr_live_llm_provider_config_validation_endpoint_reads_persisted_record_across_app_instances(
    tmp_path,
):
    first_client = TestClient(create_app(data_dir=tmp_path))
    create_response = first_client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(
            session_id="persisted_asr_provider_config_validation_review"
        ),
    )

    second_client = TestClient(create_app(data_dir=tmp_path))
    response = second_client.post(
        "/live/asr/sessions/persisted_asr_provider_config_validation_review/llm-provider-config-validation",
        json=_valid_llm_provider_config_validation_payload(),
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == "persisted_asr_provider_config_validation_review"
    assert body["validation_status"] == "valid"
    assert body["config_source_status"] == "request_body_only"
    assert body["config_file_status"] == "not_read"
    assert body["credentials_status"] == "provided_but_not_returned"
    assert body["safe_to_execute"] is False
    assert "TEST_PROVIDER_VALIDATION_SECRET_VALUE" not in response.text


def test_asr_live_llm_provider_config_validation_endpoint_returns_404_for_missing_session():
    client = TestClient(create_app())

    response = client.post(
        "/live/asr/sessions/missing_asr_review/llm-provider-config-validation",
        json=_valid_llm_provider_config_validation_payload(),
    )

    assert response.status_code == 404
    assert "ASR live session not found: missing_asr_review" in response.text
    assert "TEST_PROVIDER_VALIDATION_SECRET_VALUE" not in response.text


def test_asr_live_llm_provider_config_validation_endpoint_rejects_invalid_request_without_leaking_secret():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="local_asr_provider_config_validation_invalid"),
    )
    valid_payload = _valid_llm_provider_config_validation_payload()
    cases = [
        (
            "missing api key",
            {key: value for key, value in valid_payload.items() if key != "api_key"},
            "missing required field: api_key",
        ),
        (
            "extra field",
            {
                **valid_payload,
                "authorization": "Bearer PROVIDER_VALIDATION_SHOULD_NOT_LEAK",
            },
            "unsupported provider config field",
        ),
        (
            "bearer token extra field",
            {
                **valid_payload,
                "bearer_token": "PROVIDER_VALIDATION_BEARER_TOKEN_SHOULD_NOT_LEAK",
            },
            "unsupported provider config field",
        ),
        (
            "raw config extra field",
            {
                **valid_payload,
                "raw_config": {
                    "api_key": "PROVIDER_VALIDATION_RAW_CONFIG_SHOULD_NOT_LEAK"
                },
            },
            "unsupported provider config field",
        ),
        (
            "unsupported provider protocol",
            {**valid_payload, "provider_protocol": "custom_chat"},
            "unsupported provider_protocol",
        ),
        (
            "invalid base url",
            {**valid_payload, "base_url": "not-a-url"},
            "base_url must be an https URL",
        ),
        (
            "base url with userinfo",
            {
                **valid_payload,
                "base_url": "https://user:PROVIDER_VALIDATION_URL_SECRET@provider-validation.example.invalid/v1",
            },
            "base_url must not include credentials",
        ),
        (
            "empty api key",
            {**valid_payload, "api_key": ""},
            "api_key must be a non-empty string",
        ),
        (
            "empty model",
            {**valid_payload, "model": "  "},
            "model must be a non-empty string",
        ),
        (
            "timeout too low",
            {**valid_payload, "timeout_seconds": 0},
            "timeout_seconds must be between 1 and 120",
        ),
        (
            "timeout too high",
            {**valid_payload, "timeout_seconds": 121},
            "timeout_seconds must be between 1 and 120",
        ),
        (
            "absolute ca bundle path",
            {**valid_payload, "ca_bundle_path": "/private/root-ca.pem"},
            "ca_bundle_path must be a relative basename or subpath",
        ),
        (
            "traversal ca bundle path",
            {**valid_payload, "ca_bundle_path": "../root-ca.pem"},
            "ca_bundle_path must not contain path traversal",
        ),
    ]

    assert create_response.status_code == 201
    for _, payload, expected_detail in cases:
        response = client.post(
            "/live/asr/sessions/local_asr_provider_config_validation_invalid/llm-provider-config-validation",
            json=payload,
        )

        assert response.status_code == 422
        assert expected_detail in response.text
        assert "TEST_PROVIDER_VALIDATION_SECRET_VALUE" not in response.text
        assert "PROVIDER_VALIDATION_SHOULD_NOT_LEAK" not in response.text
        assert "PROVIDER_VALIDATION_BEARER_TOKEN_SHOULD_NOT_LEAK" not in response.text
        assert "PROVIDER_VALIDATION_RAW_CONFIG_SHOULD_NOT_LEAK" not in response.text
        assert "PROVIDER_VALIDATION_URL_SECRET" not in response.text
        assert "Bearer" not in response.text
        assert "raw_config" not in response.text


def test_asr_live_llm_provider_config_validation_endpoint_rejects_non_object_body_without_leaking_secret():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="local_asr_provider_config_validation_non_object"),
    )
    cases = [
        "TEST_PROVIDER_VALIDATION_SECRET_VALUE",
        [
            {
                "api_key": "TEST_PROVIDER_VALIDATION_SECRET_VALUE",
                "authorization": "Bearer PROVIDER_VALIDATION_SHOULD_NOT_LEAK",
            }
        ],
    ]

    assert create_response.status_code == 201
    for payload in cases:
        response = client.post(
            "/live/asr/sessions/local_asr_provider_config_validation_non_object/llm-provider-config-validation",
            json=payload,
        )

        assert response.status_code == 422
        assert "provider config request body must be an object" in response.text
        assert "TEST_PROVIDER_VALIDATION_SECRET_VALUE" not in response.text
        assert "PROVIDER_VALIDATION_SHOULD_NOT_LEAK" not in response.text
        assert "Bearer" not in response.text


def test_asr_live_llm_provider_config_loader_preflight_endpoint_returns_contract_without_reading_config(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "llm-gateway.local.json"
    config_path.write_text(
        (
            '{"base_url":"https://loader-preflight-read-sentinel.invalid",'
            '"api_key":"TEST_LOADER_PREFLIGHT_CONFIG_SECRET",'
            '"model":"loader-preflight-config-model",'
            '"authorization":"Bearer LOADER_PREFLIGHT_CONFIG_BEARER"}'
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MEETING_COPILOT_LLM_CONFIG", str(config_path))
    monkeypatch.setenv("OPENAI_API_KEY", "TEST_LOADER_PREFLIGHT_ENV_OPENAI_KEY")
    monkeypatch.setenv(
        "MEETING_COPILOT_LLM_API_KEY",
        "TEST_LOADER_PREFLIGHT_ENV_MEETING_KEY",
    )
    original_read_text = Path.read_text
    original_read_bytes = Path.read_bytes
    original_path_open = Path.open
    original_builtin_open = builtins.open
    original_getenv = os.getenv
    original_environ_get = os.environ.get

    def is_llm_config_path(path) -> bool:
        return Path(path) == config_path

    def reject_llm_config_read_text(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError(
                "llm provider config loader preflight must not read config files"
            )
        return original_read_text(path, *args, **kwargs)

    def reject_llm_config_read_bytes(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError(
                "llm provider config loader preflight must not read config bytes"
            )
        return original_read_bytes(path, *args, **kwargs)

    def reject_llm_config_path_open(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError(
                "llm provider config loader preflight must not open config files"
            )
        return original_path_open(path, *args, **kwargs)

    def reject_llm_config_builtin_open(file, *args, **kwargs):
        if is_llm_config_path(file):
            raise AssertionError(
                "llm provider config loader preflight must not open config files"
            )
        return original_builtin_open(file, *args, **kwargs)

    def reject_llm_secret_getenv(key, *args, **kwargs):
        if key in {"OPENAI_API_KEY", "MEETING_COPILOT_LLM_API_KEY"}:
            raise AssertionError(
                "llm provider config loader preflight must not read env secrets"
            )
        return original_getenv(key, *args, **kwargs)

    def reject_llm_secret_environ_get(key, *args, **kwargs):
        if key in {"OPENAI_API_KEY", "MEETING_COPILOT_LLM_API_KEY"}:
            raise AssertionError(
                "llm provider config loader preflight must not read env secrets"
            )
        return original_environ_get(key, *args, **kwargs)

    def reject_llm_gateway_config_load(*args, **kwargs):
        raise AssertionError(
            "llm provider config loader preflight must not load llm gateway config"
        )

    monkeypatch.setattr(Path, "read_text", reject_llm_config_read_text)
    monkeypatch.setattr(Path, "read_bytes", reject_llm_config_read_bytes)
    monkeypatch.setattr(Path, "open", reject_llm_config_path_open)
    monkeypatch.setattr(builtins, "open", reject_llm_config_builtin_open)
    monkeypatch.setattr(os, "getenv", reject_llm_secret_getenv)
    monkeypatch.setattr(os.environ, "get", reject_llm_secret_environ_get)
    monkeypatch.setattr(
        app_module,
        "load_llm_gateway_config",
        reject_llm_gateway_config_load,
        raising=False,
    )
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="local_asr_provider_loader_preflight_review"),
    )
    events_before_response = client.get(
        "/live/asr/sessions/local_asr_provider_loader_preflight_review/events"
    )

    response = client.post(
        "/live/asr/sessions/local_asr_provider_loader_preflight_review/llm-provider-config-loader-preflight",
        json=_valid_llm_provider_config_loader_preflight_payload(str(config_path)),
    )
    events_after_response = client.get(
        "/live/asr/sessions/local_asr_provider_loader_preflight_review/events"
    )

    assert create_response.status_code == 201
    assert events_before_response.status_code == 200
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "session_id": "local_asr_provider_loader_preflight_review",
        "source": "live_asr_stream",
        "trace_kind": "live_event",
        "preflight_kind": "provider_config_loader",
        "preflight_status": "accepted",
        "preflight_mode": "metadata_only",
        "loader_mode": "preflight_only",
        "provider_protocol": "openai_compatible_chat_completions",
        "config_source_status": "caller_supplied_path_metadata",
        "config_file_status": "not_read",
        "config_existence_status": "not_checked",
        "credentials_status": "not_read",
        "llm_call_status": "not_called",
        "schema_status": "not_generated",
        "card_status": "not_created",
        "cost_status": "not_estimated",
        "safe_to_execute": False,
        "safe_to_load_config": False,
        "path_display": {
            "config_path_label": None,
            "config_path_parent_name": None,
            "config_path": None,
        },
        "requested_fields": [
            "base_url",
            "api_key",
            "model",
            "timeout_seconds",
            "ca_bundle_path",
        ],
        "authorization_summary": {
            "user_confirmed_local_config_access": True,
            "allow_secret_read": False,
            "allow_llm_call": False,
        },
        "forbidden_response_fields": [
            "api_key",
            "authorization",
            "bearer_token",
            "raw_config",
            "config_path",
            "absolute_config_path",
        ],
        "forbidden_status_signals": [
            "config_file_exists",
            "api_key_present",
            "api_key_valid",
            "api_key_length",
            "api_key_hash",
            "api_key_prefix",
            "api_key_suffix",
            "api_key_fingerprint",
        ],
        "block_reasons": [
            "preflight_only",
            "config_file_not_read",
            "secret_read_not_authorized",
            "llm_executor_disabled",
        ],
        "next_required_decisions": [
            "secret_storage_adapter",
            "authorized_config_file_reader",
            "masked_status_loader",
            "enabled_executor_mode_contract",
        ],
    }
    assert str(config_path) not in response.text
    assert "loader-preflight-read-sentinel.invalid" not in response.text
    assert "TEST_LOADER_PREFLIGHT_CONFIG_SECRET" not in response.text
    assert "loader-preflight-config-model" not in response.text
    assert "LOADER_PREFLIGHT_CONFIG_BEARER" not in response.text
    assert "TEST_LOADER_PREFLIGHT_ENV_OPENAI_KEY" not in response.text
    assert "TEST_LOADER_PREFLIGHT_ENV_MEETING_KEY" not in response.text
    assert "Bearer" not in response.text
    assert "sk-" not in response.text
    for forbidden_key in (
        "config_file_exists",
        "api_key_present",
        "api_key_valid",
        "api_key_length",
        "api_key_hash",
        "api_key_prefix",
        "api_key_suffix",
        "api_key_fingerprint",
        "authorization",
        "bearer_token",
        "raw_config",
        "absolute_config_path",
    ):
        assert forbidden_key not in set(body)
    assert events_after_response.status_code == 200
    assert events_before_response.json()["events"] == events_after_response.json()["events"]
    assert events_after_response.json()["events"] == create_response.json()["live_events"]


def test_asr_live_llm_provider_config_loader_preflight_endpoint_accepts_transcript_only_session(
    tmp_path,
):
    client = TestClient(create_app())
    config_path = tmp_path / "llm-gateway.local.json"
    create_response = client.post(
        "/live/asr/mock/sessions",
        json={
            "session_id": "local_asr_provider_loader_preflight_empty_review",
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
        "/live/asr/sessions/local_asr_provider_loader_preflight_empty_review/llm-provider-config-loader-preflight",
        json=_valid_llm_provider_config_loader_preflight_payload(str(config_path)),
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["preflight_status"] == "accepted"
    assert body["preflight_mode"] == "metadata_only"
    assert body["config_file_status"] == "not_read"
    assert body["config_existence_status"] == "not_checked"
    assert body["credentials_status"] == "not_read"
    assert body["safe_to_load_config"] is False
    assert body["path_display"]["config_path_label"] is None
    assert body["path_display"]["config_path_parent_name"] is None
    assert body["path_display"]["config_path"] is None
    assert str(config_path) not in response.text


def test_asr_live_llm_provider_config_loader_preflight_endpoint_reads_persisted_record_across_app_instances(
    tmp_path,
):
    data_dir = tmp_path / "data"
    config_path = tmp_path / "llm-gateway.local.json"
    first_client = TestClient(create_app(data_dir=data_dir))
    create_response = first_client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="persisted_asr_provider_loader_preflight_review"),
    )

    second_client = TestClient(create_app(data_dir=data_dir))
    response = second_client.post(
        "/live/asr/sessions/persisted_asr_provider_loader_preflight_review/llm-provider-config-loader-preflight",
        json=_valid_llm_provider_config_loader_preflight_payload(str(config_path)),
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == "persisted_asr_provider_loader_preflight_review"
    assert body["preflight_status"] == "accepted"
    assert body["config_file_status"] == "not_read"
    assert body["credentials_status"] == "not_read"


def test_asr_live_llm_provider_config_loader_preflight_endpoint_returns_404_for_missing_session(
    tmp_path,
):
    client = TestClient(create_app())
    config_path = tmp_path / "llm-gateway.local.json"

    response = client.post(
        "/live/asr/sessions/missing_asr_review/llm-provider-config-loader-preflight",
        json=_valid_llm_provider_config_loader_preflight_payload(str(config_path)),
    )

    assert response.status_code == 404
    assert "ASR live session not found: missing_asr_review" in response.text
    assert str(config_path) not in response.text


def test_asr_live_llm_provider_config_loader_preflight_endpoint_rejects_invalid_request_without_leaking_path_or_secret(
    tmp_path,
):
    client = TestClient(create_app())
    config_path = tmp_path / "llm-gateway.local.json"
    valid_payload = _valid_llm_provider_config_loader_preflight_payload(str(config_path))
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="local_asr_provider_loader_preflight_invalid"),
    )
    cases = [
        (
            "non object body",
            [
                {
                    "config_path": str(config_path),
                    "api_key": "TEST_LOADER_PREFLIGHT_SHOULD_NOT_LEAK",
                }
            ],
            "provider config loader preflight request body must be an object",
        ),
        (
            "missing config path",
            {key: value for key, value in valid_payload.items() if key != "config_path"},
            "missing required field: config_path",
        ),
        (
            "extra raw config",
            {
                **valid_payload,
                "raw_config": {
                    "api_key": "TEST_LOADER_PREFLIGHT_RAW_CONFIG_SHOULD_NOT_LEAK"
                },
            },
            "unsupported provider config loader preflight field",
        ),
        (
            "unsupported mode",
            {**valid_payload, "loader_mode": "read"},
            "loader_mode must be preflight_only",
        ),
        (
            "unsupported protocol",
            {**valid_payload, "provider_protocol": "custom_chat"},
            "unsupported provider_protocol",
        ),
        (
            "empty config path",
            {**valid_payload, "config_path": "  "},
            "config_path must be a non-empty string",
        ),
        (
            "traversal config path",
            {**valid_payload, "config_path": "../llm-gateway.local.json"},
            "config_path must not contain path traversal",
        ),
        (
            "backslash traversal config path",
            {
                **valid_payload,
                "config_path": "configs\\..\\LOADER_PREFLIGHT_BACKSLASH_SECRET.json",
            },
            "config_path must not contain path traversal",
        ),
        (
            "windows drive traversal config path",
            {
                **valid_payload,
                "config_path": "C:\\safe\\..\\LOADER_PREFLIGHT_WINDOWS_SECRET.json",
            },
            "config_path must not contain path traversal",
        ),
        (
            "url config path",
            {
                **valid_payload,
                "config_path": (
                    "https://loader-preflight-url.invalid/"
                    "llm-gateway.local.json?secret=LOADER_PREFLIGHT_URL_SECRET"
                ),
            },
            "config_path must be a local filesystem path",
        ),
        (
            "file url config path",
            {
                **valid_payload,
                "config_path": (
                    "file:///private/LOADER_PREFLIGHT_FILE_URL_SECRET/"
                    "llm-gateway.local.json"
                ),
            },
            "config_path must be a local filesystem path",
        ),
        (
            "nul config path",
            {**valid_payload, "config_path": f"{config_path}\x00LOADER_PREFLIGHT_NUL_SECRET"},
            "config_path must not contain control characters",
        ),
        (
            "unsupported requested field",
            {**valid_payload, "requested_fields": ["base_url", "api_key_hash"]},
            "unsupported requested field",
        ),
        (
            "duplicate requested field",
            {**valid_payload, "requested_fields": ["base_url", "api_key", "api_key"]},
            "requested_fields must not contain duplicates",
        ),
        (
            "secret read authorized",
            {
                **valid_payload,
                "authorization": {
                    "user_confirmed_local_config_access": True,
                    "allow_secret_read": True,
                    "allow_llm_call": False,
                },
            },
            "allow_secret_read must be false during preflight",
        ),
        (
            "llm call authorized",
            {
                **valid_payload,
                "authorization": {
                    "user_confirmed_local_config_access": True,
                    "allow_secret_read": False,
                    "allow_llm_call": True,
                },
            },
            "allow_llm_call must be false during preflight",
        ),
    ]

    assert create_response.status_code == 201
    for _, payload, expected_detail in cases:
        response = client.post(
            "/live/asr/sessions/local_asr_provider_loader_preflight_invalid/llm-provider-config-loader-preflight",
            json=payload,
        )

        assert response.status_code == 422
        assert expected_detail in response.text
        assert str(config_path) not in response.text
        assert "TEST_LOADER_PREFLIGHT_SHOULD_NOT_LEAK" not in response.text
        assert "TEST_LOADER_PREFLIGHT_RAW_CONFIG_SHOULD_NOT_LEAK" not in response.text
        assert "LOADER_PREFLIGHT_URL_SECRET" not in response.text
        assert "LOADER_PREFLIGHT_FILE_URL_SECRET" not in response.text
        assert "LOADER_PREFLIGHT_NUL_SECRET" not in response.text
        assert "LOADER_PREFLIGHT_BACKSLASH_SECRET" not in response.text
        assert "LOADER_PREFLIGHT_WINDOWS_SECRET" not in response.text
        assert "api_key_hash" not in response.text


def test_asr_live_llm_provider_secret_storage_policy_endpoint_returns_template_without_reading_secrets(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "llm-gateway.local.json"
    config_path.write_text(
        (
            '{"base_url":"https://secret-storage-policy-read-sentinel.invalid",'
            '"api_key":"TEST_SECRET_STORAGE_POLICY_CONFIG_SECRET",'
            '"model":"secret-storage-policy-config-model",'
            '"authorization":"Bearer SECRET_STORAGE_POLICY_CONFIG_BEARER"}'
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MEETING_COPILOT_LLM_CONFIG", str(config_path))
    monkeypatch.setenv("OPENAI_API_KEY", "TEST_SECRET_STORAGE_POLICY_ENV_OPENAI_KEY")
    monkeypatch.setenv(
        "MEETING_COPILOT_LLM_API_KEY",
        "TEST_SECRET_STORAGE_POLICY_ENV_MEETING_KEY",
    )
    original_read_text = Path.read_text
    original_read_bytes = Path.read_bytes
    original_path_open = Path.open
    original_builtin_open = builtins.open
    original_getenv = os.getenv
    original_environ_get = os.environ.get

    def is_llm_config_path(path) -> bool:
        return Path(path) == config_path

    def reject_llm_config_read_text(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError(
                "llm provider secret storage policy must not read config files"
            )
        return original_read_text(path, *args, **kwargs)

    def reject_llm_config_read_bytes(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError(
                "llm provider secret storage policy must not read config bytes"
            )
        return original_read_bytes(path, *args, **kwargs)

    def reject_llm_config_path_open(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError(
                "llm provider secret storage policy must not open config files"
            )
        return original_path_open(path, *args, **kwargs)

    def reject_llm_config_builtin_open(file, *args, **kwargs):
        if is_llm_config_path(file):
            raise AssertionError(
                "llm provider secret storage policy must not open config files"
            )
        return original_builtin_open(file, *args, **kwargs)

    def reject_llm_secret_getenv(key, *args, **kwargs):
        if key in {"OPENAI_API_KEY", "MEETING_COPILOT_LLM_API_KEY"}:
            raise AssertionError(
                "llm provider secret storage policy must not read env secrets"
            )
        return original_getenv(key, *args, **kwargs)

    def reject_llm_secret_environ_get(key, *args, **kwargs):
        if key in {"OPENAI_API_KEY", "MEETING_COPILOT_LLM_API_KEY"}:
            raise AssertionError(
                "llm provider secret storage policy must not read env secrets"
            )
        return original_environ_get(key, *args, **kwargs)

    def reject_llm_gateway_config_load(*args, **kwargs):
        raise AssertionError(
            "llm provider secret storage policy must not load llm gateway config"
        )

    def reject_keychain_access(*args, **kwargs):
        raise AssertionError(
            "llm provider secret storage policy must not access keychain"
        )

    monkeypatch.setattr(Path, "read_text", reject_llm_config_read_text)
    monkeypatch.setattr(Path, "read_bytes", reject_llm_config_read_bytes)
    monkeypatch.setattr(Path, "open", reject_llm_config_path_open)
    monkeypatch.setattr(builtins, "open", reject_llm_config_builtin_open)
    monkeypatch.setattr(os, "getenv", reject_llm_secret_getenv)
    monkeypatch.setattr(os.environ, "get", reject_llm_secret_environ_get)
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
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="local_asr_provider_secret_storage_policy"),
    )
    events_before_response = client.get(
        "/live/asr/sessions/local_asr_provider_secret_storage_policy/events"
    )

    response = client.get(
        "/live/asr/sessions/local_asr_provider_secret_storage_policy/llm-provider-secret-storage-policy"
    )
    events_after_response = client.get(
        "/live/asr/sessions/local_asr_provider_secret_storage_policy/events"
    )

    assert create_response.status_code == 201
    assert events_before_response.status_code == 200
    assert response.status_code == 200
    _assert_llm_provider_secret_storage_policy_body(
        response.json(),
        "local_asr_provider_secret_storage_policy",
    )
    assert str(config_path) not in response.text
    assert "secret-storage-policy-read-sentinel.invalid" not in response.text
    assert "TEST_SECRET_STORAGE_POLICY_CONFIG_SECRET" not in response.text
    assert "secret-storage-policy-config-model" not in response.text
    assert "SECRET_STORAGE_POLICY_CONFIG_BEARER" not in response.text
    assert "TEST_SECRET_STORAGE_POLICY_ENV_OPENAI_KEY" not in response.text
    assert "TEST_SECRET_STORAGE_POLICY_ENV_MEETING_KEY" not in response.text
    assert "Bearer" not in response.text
    assert "sk-" not in response.text
    for forbidden_key in (
        "api_key_present",
        "api_key_valid",
        "api_key_length",
        "api_key_hash",
        "api_key_prefix",
        "api_key_suffix",
        "api_key_fingerprint",
        "authorization",
        "bearer_token",
        "raw_config",
        "masked_api_key",
    ):
        assert forbidden_key not in set(response.json())
    assert events_after_response.status_code == 200
    assert events_before_response.json()["events"] == events_after_response.json()["events"]
    assert events_after_response.json()["events"] == create_response.json()["live_events"]


def test_asr_live_llm_provider_secret_storage_policy_endpoint_accepts_transcript_only_session():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json={
            "session_id": "local_asr_provider_secret_storage_policy_empty",
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
        "/live/asr/sessions/local_asr_provider_secret_storage_policy_empty/llm-provider-secret-storage-policy"
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["policy_status"] == "template_only"
    assert body["secret_storage_status"] == "not_connected"
    assert body["credentials_status"] == "not_read"
    assert body["safe_to_read_secret"] is False
    assert body["safe_to_execute"] is False


def test_asr_live_llm_provider_secret_storage_policy_endpoint_reads_persisted_record_across_app_instances(
    tmp_path,
):
    first_client = TestClient(create_app(data_dir=tmp_path))
    create_response = first_client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="persisted_asr_provider_secret_storage_policy"),
    )

    second_client = TestClient(create_app(data_dir=tmp_path))
    response = second_client.get(
        "/live/asr/sessions/persisted_asr_provider_secret_storage_policy/llm-provider-secret-storage-policy"
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == "persisted_asr_provider_secret_storage_policy"
    assert body["policy_kind"] == "provider_secret_storage"
    assert body["config_source_status"] == "not_read"
    assert body["credentials_status"] == "not_read"


def test_asr_live_llm_provider_secret_storage_policy_endpoint_returns_404_for_missing_session():
    client = TestClient(create_app())

    response = client.get(
        "/live/asr/sessions/missing_asr_review/llm-provider-secret-storage-policy"
    )

    assert response.status_code == 404
    assert "ASR live session not found: missing_asr_review" in response.text


def test_asr_live_llm_provider_config_reader_dry_run_endpoint_returns_contract_without_reading_config_or_secret(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "llm-gateway.local.json"
    config_path.write_text(
        (
            '{"base_url":"https://config-reader-dry-run-read-sentinel.invalid",'
            '"api_key":"TEST_CONFIG_READER_DRY_RUN_CONFIG_SECRET",'
            '"model":"config-reader-dry-run-config-model",'
            '"authorization":"Bearer CONFIG_READER_DRY_RUN_CONFIG_BEARER"}'
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MEETING_COPILOT_LLM_CONFIG", str(config_path))
    monkeypatch.setenv("OPENAI_API_KEY", "TEST_CONFIG_READER_DRY_RUN_ENV_OPENAI_KEY")
    monkeypatch.setenv(
        "MEETING_COPILOT_LLM_API_KEY",
        "TEST_CONFIG_READER_DRY_RUN_ENV_MEETING_KEY",
    )
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

    def is_llm_config_path(path) -> bool:
        return Path(path) == config_path

    def reject_llm_config_read_text(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError(
                "llm provider config reader dry run must not read config files"
            )
        return original_read_text(path, *args, **kwargs)

    def reject_llm_config_read_bytes(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError(
                "llm provider config reader dry run must not read config bytes"
            )
        return original_read_bytes(path, *args, **kwargs)

    def reject_llm_config_path_open(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError(
                "llm provider config reader dry run must not open config files"
            )
        return original_path_open(path, *args, **kwargs)

    def reject_llm_config_builtin_open(file, *args, **kwargs):
        if is_llm_config_path(file):
            raise AssertionError(
                "llm provider config reader dry run must not open config files"
            )
        return original_builtin_open(file, *args, **kwargs)

    def reject_llm_config_exists(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError(
                "llm provider config reader dry run must not check config existence"
            )
        return original_path_exists(path, *args, **kwargs)

    def reject_llm_config_is_file(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError(
                "llm provider config reader dry run must not check config file type"
            )
        return original_path_is_file(path, *args, **kwargs)

    def reject_llm_config_path_stat(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError(
                "llm provider config reader dry run must not stat config files"
            )
        return original_path_stat(path, *args, **kwargs)

    def reject_llm_config_os_stat(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError(
                "llm provider config reader dry run must not stat config files"
            )
        return original_os_stat(path, *args, **kwargs)

    def reject_llm_secret_getenv(key, *args, **kwargs):
        if key in {"OPENAI_API_KEY", "MEETING_COPILOT_LLM_API_KEY"}:
            raise AssertionError(
                "llm provider config reader dry run must not read env secrets"
            )
        return original_getenv(key, *args, **kwargs)

    def reject_llm_secret_environ_get(key, *args, **kwargs):
        if key in {"OPENAI_API_KEY", "MEETING_COPILOT_LLM_API_KEY"}:
            raise AssertionError(
                "llm provider config reader dry run must not read env secrets"
            )
        return original_environ_get(key, *args, **kwargs)

    def reject_llm_gateway_config_load(*args, **kwargs):
        raise AssertionError(
            "llm provider config reader dry run must not load llm gateway config"
        )

    def reject_keychain_access(*args, **kwargs):
        raise AssertionError("llm provider config reader dry run must not access keychain")

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
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="local_asr_provider_config_reader_dry_run"),
    )
    events_before_response = client.get(
        "/live/asr/sessions/local_asr_provider_config_reader_dry_run/events"
    )
    payload = _valid_llm_provider_config_reader_dry_run_payload(str(config_path))

    response = client.post(
        "/live/asr/sessions/local_asr_provider_config_reader_dry_run/llm-provider-config-reader-dry-run",
        json=payload,
    )
    events_after_response = client.get(
        "/live/asr/sessions/local_asr_provider_config_reader_dry_run/events"
    )

    assert create_response.status_code == 201
    assert events_before_response.status_code == 200
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "session_id": "local_asr_provider_config_reader_dry_run",
        "source": "live_asr_stream",
        "trace_kind": "live_event",
        "dry_run_kind": "authorized_config_file_reader",
        "dry_run_status": "blocked",
        "dry_run_mode": "dry_run_only",
        "provider_protocol": "openai_compatible_chat_completions",
        "config_source_status": "caller_supplied_path_reference",
        "config_file_status": "not_read",
        "config_existence_status": "not_checked",
        "secret_reference_status": "provided_not_resolved",
        "secret_storage_status": "not_connected",
        "credentials_status": "not_read",
        "llm_call_status": "not_called",
        "schema_status": "not_generated",
        "card_status": "not_created",
        "cost_status": "not_estimated",
        "safe_to_read_config": False,
        "safe_to_read_secret": False,
        "safe_to_execute": False,
        "path_display": {
            "config_path_label": None,
            "config_path_parent_name": None,
            "config_path": None,
        },
        "secret_reference_display": {
            "reference_type": "keychain_item_reference",
            "reference_id": None,
        },
        "authorization_summary": {
            "user_confirmed_local_config_access": True,
            "acknowledged_secret_storage_policy": True,
            "allow_config_file_read": False,
            "allow_secret_read": False,
            "allow_llm_call": False,
            "allow_event_mutation": False,
        },
        "required_loader_guards": [
            "explicit_user_authorization",
            "path_privacy_redaction",
            "secret_reference_only",
            "secret_value_redaction",
            "no_secret_in_error_response",
            "no_secret_in_audit_event",
            "no_secret_in_logs",
            "no_secret_in_browser_storage",
        ],
        "forbidden_response_fields": [
            "api_key",
            "authorization",
            "bearer_token",
            "raw_config",
            "config_path",
            "absolute_config_path",
            "secret_reference_id",
            "masked_api_key",
            "api_key_hash",
            "api_key_prefix",
            "api_key_suffix",
            "api_key_length",
            "api_key_fingerprint",
        ],
        "forbidden_status_signals": [
            "config_file_exists",
            "config_file_readable",
            "config_file_size",
            "config_file_mtime",
            "config_file_hash",
            "api_key_present",
            "api_key_valid",
            "api_key_length",
            "api_key_hash",
            "api_key_prefix",
            "api_key_suffix",
            "api_key_fingerprint",
        ],
        "block_reasons": [
            "dry_run_only",
            "config_file_read_not_authorized",
            "secret_value_read_not_authorized",
            "secret_storage_adapter_not_connected",
            "llm_executor_disabled",
        ],
        "next_required_decisions": [
            "authorized_config_file_reader",
            "os_keychain_adapter",
            "enterprise_secret_provider_adapter",
            "authorized_masked_status_loader",
            "enabled_executor_mode_contract",
        ],
    }
    assert str(config_path) not in response.text
    assert "config-reader-dry-run-read-sentinel.invalid" not in response.text
    assert "TEST_CONFIG_READER_DRY_RUN_CONFIG_SECRET" not in response.text
    assert "config-reader-dry-run-config-model" not in response.text
    assert "CONFIG_READER_DRY_RUN_CONFIG_BEARER" not in response.text
    assert "TEST_CONFIG_READER_DRY_RUN_ENV_OPENAI_KEY" not in response.text
    assert "TEST_CONFIG_READER_DRY_RUN_ENV_MEETING_KEY" not in response.text
    assert payload["secret_reference"]["reference_id"] not in response.text
    assert "Bearer" not in response.text
    assert "sk-" not in response.text
    assert events_after_response.status_code == 200
    assert events_before_response.json()["events"] == events_after_response.json()["events"]
    assert events_after_response.json()["events"] == create_response.json()["live_events"]


def test_asr_live_llm_provider_config_reader_dry_run_endpoint_accepts_transcript_only_session():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json={
            "session_id": "local_asr_provider_config_reader_dry_run_empty",
            "provider": "local_mock_asr",
            "streaming_events": [
                {
                    "event_type": "final",
                    "segment_id": "asr_seg_reader_transcript_only_001",
                    "text": "这段会议先只确认背景。",
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
    payload = _valid_llm_provider_config_reader_dry_run_payload(
        "configs/local/reader-transcript-only.json"
    )

    response = client.post(
        "/live/asr/sessions/local_asr_provider_config_reader_dry_run_empty/llm-provider-config-reader-dry-run",
        json=payload,
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == "local_asr_provider_config_reader_dry_run_empty"
    assert body["dry_run_status"] == "blocked"
    assert body["config_file_status"] == "not_read"
    assert body["credentials_status"] == "not_read"
    assert body["safe_to_execute"] is False
    _assert_config_reader_dry_run_response_redacts_submitted_values(
        response,
        "configs/local/reader-transcript-only.json",
        "reader-transcript-only.json",
        payload["secret_reference"]["reference_id"],
    )


def test_asr_live_llm_provider_config_reader_dry_run_endpoint_reads_persisted_record_across_app_instances(
    tmp_path,
):
    first_client = TestClient(create_app(data_dir=tmp_path))
    create_response = first_client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="persisted_asr_provider_config_reader_dry_run"),
    )
    payload = _valid_llm_provider_config_reader_dry_run_payload(
        "configs/local/persisted-reader-dry-run.json"
    )

    second_client = TestClient(create_app(data_dir=tmp_path))
    response = second_client.post(
        "/live/asr/sessions/persisted_asr_provider_config_reader_dry_run/llm-provider-config-reader-dry-run",
        json=payload,
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == "persisted_asr_provider_config_reader_dry_run"
    assert body["dry_run_kind"] == "authorized_config_file_reader"
    assert body["config_existence_status"] == "not_checked"
    assert body["secret_reference_status"] == "provided_not_resolved"
    _assert_config_reader_dry_run_response_redacts_submitted_values(
        response,
        "configs/local/persisted-reader-dry-run.json",
        "persisted-reader-dry-run.json",
        payload["secret_reference"]["reference_id"],
    )


def test_asr_live_llm_provider_config_reader_dry_run_endpoint_returns_404_without_leaking_submitted_path_or_secret_reference():
    client = TestClient(create_app())
    payload = _valid_llm_provider_config_reader_dry_run_payload(
        "/Users/example/private/reader-missing-session-secret-config.json"
    )
    payload["secret_reference"][
        "reference_id"
    ] = "meeting-copilot/missing-session-provider-config-secret"

    response = client.post(
        "/live/asr/sessions/missing_asr_provider_config_reader_dry_run/llm-provider-config-reader-dry-run",
        json=payload,
    )

    assert response.status_code == 404
    assert "ASR live session not found: missing_asr_provider_config_reader_dry_run" in response.text
    _assert_config_reader_dry_run_response_redacts_submitted_values(
        response,
        "/Users/example/private/reader-missing-session-secret-config.json",
        "reader-missing-session-secret-config.json",
        "missing-session-provider-config-secret",
        payload["secret_reference"]["reference_id"],
    )


def test_asr_live_llm_provider_config_reader_dry_run_endpoint_rejects_invalid_requests_without_leaking_submitted_values():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(session_id="local_asr_provider_config_reader_invalid"),
    )
    assert create_response.status_code == 201

    sentinel_path = "/Users/example/private/reader-invalid-secret-config.json"
    sentinel_reference = "meeting-copilot/reader-invalid-secret-reference"
    base_payload = _valid_llm_provider_config_reader_dry_run_payload(sentinel_path)
    base_payload["secret_reference"]["reference_id"] = sentinel_reference

    invalid_cases = [
        (
            "non-object body",
            "reader-invalid-secret-config.json",
            "provider config reader dry run request body must be an object",
        ),
        (
            "missing top-level field",
            {key: value for key, value in base_payload.items() if key != "config_path"},
            "provider config reader dry run fields must match contract",
        ),
        (
            "extra raw config field",
            {**base_payload, "raw_config": {"api_key": "TEST_READER_RAW_CONFIG_SECRET"}},
            "provider config reader dry run fields must match contract",
        ),
        (
            "extra api key field",
            {**base_payload, "api_key": "TEST_READER_EXTRA_API_KEY"},
            "provider config reader dry run fields must match contract",
        ),
        (
            "extra authorization header",
            {**base_payload, "authorization_header": "Bearer TEST_READER_EXTRA_BEARER"},
            "provider config reader dry run fields must match contract",
        ),
        (
            "unsupported reader mode",
            {**base_payload, "reader_mode": "read_config"},
            "reader_mode must be dry_run_only",
        ),
        (
            "unsupported protocol",
            {**base_payload, "provider_protocol": "custom_provider_protocol"},
            "unsupported provider_protocol",
        ),
        (
            "empty config path",
            {**base_payload, "config_path": " "},
            "config_path must be a non-empty string",
        ),
        (
            "url config path",
            {**base_payload, "config_path": "https://reader-invalid.example/config.json"},
            "config_path must be a local filesystem path",
        ),
        (
            "file url config path",
            {**base_payload, "config_path": "file:///Users/example/private/config.json"},
            "config_path must be a local filesystem path",
        ),
        (
            "nul config path",
            {**base_payload, "config_path": "configs/local/reader\x00secret.json"},
            "config_path must not contain control characters",
        ),
        (
            "del config path",
            {**base_payload, "config_path": "configs/local/reader\x7fsecret.json"},
            "config_path must not contain control characters",
        ),
        (
            "posix traversal config path",
            {**base_payload, "config_path": "../configs/local/reader-secret.json"},
            "config_path must not contain path traversal",
        ),
        (
            "windows traversal config path",
            {**base_payload, "config_path": r"configs\..\secret.json"},
            "config_path must not contain path traversal",
        ),
        (
            "secret reference not object",
            {**base_payload, "secret_reference": "reader-secret-reference"},
            "secret_reference must be an object",
        ),
        (
            "secret reference extra field",
            {
                **base_payload,
                "secret_reference": {
                    **base_payload["secret_reference"],
                    "api_key": "TEST_READER_SECRET_REFERENCE_API_KEY",
                },
            },
            "secret_reference fields must match contract",
        ),
        (
            "non-string reference type",
            {
                **base_payload,
                "secret_reference": {
                    **base_payload["secret_reference"],
                    "reference_type": 123,
                },
            },
            "secret_reference reference_type must be a string",
        ),
        (
            "unsupported reference type",
            {
                **base_payload,
                "secret_reference": {
                    **base_payload["secret_reference"],
                    "reference_type": "plaintext_api_key",
                },
            },
            "unsupported secret_reference reference_type",
        ),
        (
            "empty reference id",
            {
                **base_payload,
                "secret_reference": {
                    **base_payload["secret_reference"],
                    "reference_id": " ",
                },
            },
            "secret_reference reference_id must be a non-empty string",
        ),
        (
            "control character reference id",
            {
                **base_payload,
                "secret_reference": {
                    **base_payload["secret_reference"],
                    "reference_id": "reader-secret\x00reference",
                },
            },
            "secret_reference reference_id must not contain control characters",
        ),
        (
            "del character reference id",
            {
                **base_payload,
                "secret_reference": {
                    **base_payload["secret_reference"],
                    "reference_id": "reader-secret\x7freference",
                },
            },
            "secret_reference reference_id must not contain control characters",
        ),
        (
            "authorization not object",
            {**base_payload, "authorization": "true"},
            "authorization must be an object",
        ),
        (
            "authorization missing storage acknowledgement",
            {
                **base_payload,
                "authorization": {
                    key: value
                    for key, value in base_payload["authorization"].items()
                    if key != "acknowledged_secret_storage_policy"
                },
            },
            "authorization fields must match dry run contract",
        ),
        (
            "allow config file read true",
            {
                **base_payload,
                "authorization": {
                    **base_payload["authorization"],
                    "allow_config_file_read": True,
                },
            },
            "allow_config_file_read must be false during dry run",
        ),
        (
            "allow secret read true",
            {
                **base_payload,
                "authorization": {
                    **base_payload["authorization"],
                    "allow_secret_read": True,
                },
            },
            "allow_secret_read must be false during dry run",
        ),
        (
            "allow llm call true",
            {
                **base_payload,
                "authorization": {
                    **base_payload["authorization"],
                    "allow_llm_call": True,
                },
            },
            "allow_llm_call must be false during dry run",
        ),
        (
            "allow event mutation true",
            {
                **base_payload,
                "authorization": {
                    **base_payload["authorization"],
                    "allow_event_mutation": True,
                },
            },
            "allow_event_mutation must be false during dry run",
        ),
        (
            "missing user confirmation",
            {
                **base_payload,
                "authorization": {
                    **base_payload["authorization"],
                    "user_confirmed_local_config_access": False,
                },
            },
            "user_confirmed_local_config_access must be true",
        ),
        (
            "missing storage policy acknowledgement value",
            {
                **base_payload,
                "authorization": {
                    **base_payload["authorization"],
                    "acknowledged_secret_storage_policy": False,
                },
            },
            "acknowledged_secret_storage_policy must be true",
        ),
    ]

    for case_name, payload, expected_detail in invalid_cases:
        response = client.post(
            "/live/asr/sessions/local_asr_provider_config_reader_invalid/llm-provider-config-reader-dry-run",
            json=payload,
        )

        assert response.status_code == 422, case_name
        assert response.json()["detail"] == expected_detail
        _assert_config_reader_dry_run_response_redacts_submitted_values(
            response,
            sentinel_path,
            "reader-invalid-secret-config.json",
            sentinel_reference,
            "TEST_READER_RAW_CONFIG_SECRET",
            "TEST_READER_EXTRA_API_KEY",
            "TEST_READER_EXTRA_BEARER",
            "TEST_READER_SECRET_REFERENCE_API_KEY",
            "reader-invalid.example",
            "reader-secret-reference",
        )


def test_asr_live_llm_provider_masked_status_loader_dry_run_endpoint_returns_contract_without_reading_or_inferring_status(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "llm-gateway.local.json"
    config_path.write_text(
        (
            '{"base_url":"https://masked-status-loader-dry-run-read-sentinel.invalid",'
            '"api_key":"TEST_MASKED_STATUS_LOADER_DRY_RUN_CONFIG_SECRET",'
            '"model":"masked-status-loader-dry-run-config-model",'
            '"timeout_seconds":37,'
            '"ca_bundle_path":"/very/private/masked-status-loader-root-ca.pem",'
            '"authorization":"Bearer MASKED_STATUS_LOADER_DRY_RUN_CONFIG_BEARER"}'
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MEETING_COPILOT_LLM_CONFIG", str(config_path))
    monkeypatch.setenv(
        "OPENAI_API_KEY",
        "TEST_MASKED_STATUS_LOADER_DRY_RUN_ENV_OPENAI_KEY",
    )
    monkeypatch.setenv(
        "MEETING_COPILOT_LLM_API_KEY",
        "TEST_MASKED_STATUS_LOADER_DRY_RUN_ENV_MEETING_KEY",
    )
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

    def is_llm_config_path(path) -> bool:
        return Path(path) == config_path

    def reject_llm_config_read_text(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError(
                "llm provider masked status loader dry run must not read config files"
            )
        return original_read_text(path, *args, **kwargs)

    def reject_llm_config_read_bytes(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError(
                "llm provider masked status loader dry run must not read config bytes"
            )
        return original_read_bytes(path, *args, **kwargs)

    def reject_llm_config_path_open(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError(
                "llm provider masked status loader dry run must not open config files"
            )
        return original_path_open(path, *args, **kwargs)

    def reject_llm_config_builtin_open(file, *args, **kwargs):
        if is_llm_config_path(file):
            raise AssertionError(
                "llm provider masked status loader dry run must not open config files"
            )
        return original_builtin_open(file, *args, **kwargs)

    def reject_llm_config_exists(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError(
                "llm provider masked status loader dry run must not check config existence"
            )
        return original_path_exists(path, *args, **kwargs)

    def reject_llm_config_is_file(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError(
                "llm provider masked status loader dry run must not check config file type"
            )
        return original_path_is_file(path, *args, **kwargs)

    def reject_llm_config_path_stat(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError(
                "llm provider masked status loader dry run must not stat config files"
            )
        return original_path_stat(path, *args, **kwargs)

    def reject_llm_config_os_stat(path, *args, **kwargs):
        if is_llm_config_path(path):
            raise AssertionError(
                "llm provider masked status loader dry run must not stat config files"
            )
        return original_os_stat(path, *args, **kwargs)

    def reject_llm_secret_getenv(key, *args, **kwargs):
        if key in {"OPENAI_API_KEY", "MEETING_COPILOT_LLM_API_KEY"}:
            raise AssertionError(
                "llm provider masked status loader dry run must not read env secrets"
            )
        return original_getenv(key, *args, **kwargs)

    def reject_llm_secret_environ_get(key, *args, **kwargs):
        if key in {"OPENAI_API_KEY", "MEETING_COPILOT_LLM_API_KEY"}:
            raise AssertionError(
                "llm provider masked status loader dry run must not read env secrets"
            )
        return original_environ_get(key, *args, **kwargs)

    def reject_llm_gateway_config_load(*args, **kwargs):
        raise AssertionError(
            "llm provider masked status loader dry run must not load llm gateway config"
        )

    def reject_keychain_access(*args, **kwargs):
        raise AssertionError(
            "llm provider masked status loader dry run must not access keychain"
        )

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
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(
            session_id="local_asr_provider_masked_status_loader_dry_run"
        ),
    )
    events_before_response = client.get(
        "/live/asr/sessions/local_asr_provider_masked_status_loader_dry_run/events"
    )
    payload = _valid_llm_provider_masked_status_loader_dry_run_payload(
        str(config_path)
    )

    response = client.post(
        "/live/asr/sessions/local_asr_provider_masked_status_loader_dry_run/llm-provider-masked-status-loader-dry-run",
        json=payload,
    )
    events_after_response = client.get(
        "/live/asr/sessions/local_asr_provider_masked_status_loader_dry_run/events"
    )

    assert create_response.status_code == 201
    assert events_before_response.status_code == 200
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "session_id": "local_asr_provider_masked_status_loader_dry_run",
        "source": "live_asr_stream",
        "trace_kind": "live_event",
        "dry_run_kind": "authorized_masked_status_loader",
        "dry_run_status": "blocked",
        "dry_run_mode": "masked_status_dry_run_only",
        "provider_protocol": "openai_compatible_chat_completions",
        "config_source_status": "caller_supplied_path_reference",
        "config_file_status": "not_read",
        "config_existence_status": "not_checked",
        "secret_reference_status": "provided_not_resolved",
        "secret_storage_status": "not_connected",
        "credentials_status": "not_read",
        "status_value_status": "not_inferred",
        "llm_call_status": "not_called",
        "schema_status": "not_generated",
        "card_status": "not_created",
        "cost_status": "not_estimated",
        "safe_to_read_config": False,
        "safe_to_read_secret": False,
        "safe_to_infer_status": False,
        "safe_to_execute": False,
        "path_display": {
            "config_path_label": None,
            "config_path_parent_name": None,
            "config_path": None,
        },
        "secret_reference_display": {
            "reference_type": "keychain_item_reference",
            "reference_id": None,
        },
        "requested_display_fields": [
            "base_url_origin",
            "model",
            "timeout_seconds",
            "ca_bundle_name",
            "api_key",
        ],
        "display_values": {
            "base_url_origin": None,
            "model": None,
            "timeout_seconds": None,
            "ca_bundle_name": None,
            "api_key": None,
        },
        "display_value_status": {
            "base_url_origin": "not_read",
            "model": "not_read",
            "timeout_seconds": "not_read",
            "ca_bundle_name": "not_read",
            "api_key": "never_display",
        },
        "masked_value_policy": {
            "api_key": "never_return_value_or_mask",
            "base_url": "origin_only_after_authorized_loader",
            "model": "display_allowed_after_authorized_loader",
            "timeout_seconds": "display_allowed_after_authorized_loader",
            "ca_bundle_path": "basename_only_after_authorized_loader",
        },
        "authorization_summary": {
            "user_confirmed_local_config_access": True,
            "acknowledged_secret_storage_policy": True,
            "allow_config_file_read": False,
            "allow_secret_read": False,
            "allow_llm_call": False,
            "allow_event_mutation": False,
            "allow_status_value_inference": False,
        },
        "forbidden_response_fields": [
            "api_key",
            "authorization",
            "bearer_token",
            "raw_config",
            "config_path",
            "absolute_config_path",
            "secret_reference_id",
            "masked_api_key",
            "api_key_hash",
            "api_key_prefix",
            "api_key_suffix",
            "api_key_length",
            "api_key_fingerprint",
        ],
        "forbidden_status_signals": [
            "config_file_exists",
            "config_file_readable",
            "config_file_size",
            "config_file_mtime",
            "config_file_hash",
            "api_key_present",
            "api_key_valid",
            "api_key_length",
            "api_key_hash",
            "api_key_prefix",
            "api_key_suffix",
            "api_key_fingerprint",
        ],
        "block_reasons": [
            "dry_run_only",
            "config_file_read_not_authorized",
            "secret_value_read_not_authorized",
            "status_value_inference_not_authorized",
            "secret_storage_adapter_not_connected",
            "llm_executor_disabled",
        ],
        "next_required_decisions": [
            "authorized_config_file_reader",
            "os_keychain_adapter",
            "enterprise_secret_provider_adapter",
            "enabled_executor_mode_contract",
            "schema_validation_and_card_lifecycle",
        ],
    }
    _assert_masked_status_loader_dry_run_response_redacts_submitted_values(
        response,
        str(config_path),
        "llm-gateway.local.json",
        "masked-status-loader-dry-run-read-sentinel.invalid",
        "TEST_MASKED_STATUS_LOADER_DRY_RUN_CONFIG_SECRET",
        "masked-status-loader-dry-run-config-model",
        "masked-status-loader-root-ca.pem",
        "MASKED_STATUS_LOADER_DRY_RUN_CONFIG_BEARER",
        "TEST_MASKED_STATUS_LOADER_DRY_RUN_ENV_OPENAI_KEY",
        "TEST_MASKED_STATUS_LOADER_DRY_RUN_ENV_MEETING_KEY",
        payload["secret_reference"]["reference_id"],
    )
    assert body["display_values"]["api_key"] is None
    assert body["display_value_status"]["api_key"] == "never_display"
    assert body["masked_value_policy"]["api_key"] == "never_return_value_or_mask"
    assert events_after_response.status_code == 200
    assert events_before_response.json()["events"] == events_after_response.json()["events"]
    assert events_after_response.json()["events"] == create_response.json()["live_events"]


def test_asr_live_llm_provider_masked_status_loader_dry_run_endpoint_accepts_transcript_only_session():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json={
            "session_id": "local_asr_provider_masked_status_loader_dry_run_empty",
            "provider": "local_mock_asr",
            "streaming_events": [
                {
                    "event_type": "final",
                    "segment_id": "asr_seg_masked_loader_transcript_only_001",
                    "text": "先同步一下会议背景。",
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
    payload = _valid_llm_provider_masked_status_loader_dry_run_payload(
        "configs/local/masked-status-loader-transcript-only.json"
    )

    response = client.post(
        "/live/asr/sessions/local_asr_provider_masked_status_loader_dry_run_empty/llm-provider-masked-status-loader-dry-run",
        json=payload,
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == "local_asr_provider_masked_status_loader_dry_run_empty"
    assert body["dry_run_status"] == "blocked"
    assert body["status_value_status"] == "not_inferred"
    assert body["safe_to_infer_status"] is False
    assert body["display_values"]["api_key"] is None
    _assert_masked_status_loader_dry_run_response_redacts_submitted_values(
        response,
        "configs/local/masked-status-loader-transcript-only.json",
        "masked-status-loader-transcript-only.json",
        payload["secret_reference"]["reference_id"],
    )


def test_asr_live_llm_provider_masked_status_loader_dry_run_endpoint_reads_persisted_record_across_app_instances(
    tmp_path,
):
    first_client = TestClient(create_app(data_dir=tmp_path))
    create_response = first_client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(
            session_id="persisted_asr_provider_masked_status_loader_dry_run"
        ),
    )
    payload = _valid_llm_provider_masked_status_loader_dry_run_payload(
        "configs/local/persisted-masked-status-loader.json"
    )

    second_client = TestClient(create_app(data_dir=tmp_path))
    response = second_client.post(
        "/live/asr/sessions/persisted_asr_provider_masked_status_loader_dry_run/llm-provider-masked-status-loader-dry-run",
        json=payload,
    )

    assert create_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == "persisted_asr_provider_masked_status_loader_dry_run"
    assert body["dry_run_kind"] == "authorized_masked_status_loader"
    assert body["config_existence_status"] == "not_checked"
    assert body["secret_reference_status"] == "provided_not_resolved"
    assert body["status_value_status"] == "not_inferred"
    _assert_masked_status_loader_dry_run_response_redacts_submitted_values(
        response,
        "configs/local/persisted-masked-status-loader.json",
        "persisted-masked-status-loader.json",
        payload["secret_reference"]["reference_id"],
    )


def test_asr_live_llm_provider_masked_status_loader_dry_run_endpoint_returns_404_without_leaking_submitted_path_or_secret_reference():
    client = TestClient(create_app())
    payload = _valid_llm_provider_masked_status_loader_dry_run_payload(
        "/Users/example/private/masked-status-loader-missing-session-config.json"
    )
    payload["secret_reference"][
        "reference_id"
    ] = "meeting-copilot/masked-status-loader-missing-session-secret"

    response = client.post(
        "/live/asr/sessions/missing_asr_provider_masked_status_loader_dry_run/llm-provider-masked-status-loader-dry-run",
        json=payload,
    )

    assert response.status_code == 404
    assert "ASR live session not found: missing_asr_provider_masked_status_loader_dry_run" in response.text
    _assert_masked_status_loader_dry_run_response_redacts_submitted_values(
        response,
        "/Users/example/private/masked-status-loader-missing-session-config.json",
        "masked-status-loader-missing-session-config.json",
        "masked-status-loader-missing-session-secret",
        payload["secret_reference"]["reference_id"],
    )


def test_asr_live_llm_provider_masked_status_loader_dry_run_endpoint_rejects_invalid_requests_without_leaking_submitted_values():
    client = TestClient(create_app())
    create_response = client.post(
        "/live/asr/mock/sessions",
        json=_asr_live_payload(
            session_id="local_asr_provider_masked_status_loader_invalid"
        ),
    )
    assert create_response.status_code == 201

    sentinel_path = "/Users/example/private/masked-status-loader-invalid-secret-config.json"
    sentinel_reference = "meeting-copilot/masked-status-loader-invalid-secret-reference"
    base_payload = _valid_llm_provider_masked_status_loader_dry_run_payload(
        sentinel_path
    )
    base_payload["secret_reference"]["reference_id"] = sentinel_reference

    invalid_cases = [
        (
            "non-object body",
            "masked-status-loader-invalid-secret-config.json",
            "provider masked status loader dry run request body must be an object",
        ),
        (
            "missing top-level field",
            {
                key: value
                for key, value in base_payload.items()
                if key != "requested_display_fields"
            },
            "provider masked status loader dry run fields must match contract",
        ),
        (
            "extra raw config field",
            {
                **base_payload,
                "raw_config": {"api_key": "TEST_MASKED_STATUS_LOADER_RAW_CONFIG_SECRET"},
            },
            "provider masked status loader dry run fields must match contract",
        ),
        (
            "extra api key field",
            {**base_payload, "api_key": "TEST_MASKED_STATUS_LOADER_EXTRA_API_KEY"},
            "provider masked status loader dry run fields must match contract",
        ),
        (
            "extra authorization header",
            {
                **base_payload,
                "authorization_header": "Bearer TEST_MASKED_STATUS_LOADER_EXTRA_BEARER",
            },
            "provider masked status loader dry run fields must match contract",
        ),
        (
            "unsupported loader mode",
            {**base_payload, "loader_mode": "load_masked_status"},
            "loader_mode must be masked_status_dry_run_only",
        ),
        (
            "unsupported protocol",
            {**base_payload, "provider_protocol": "custom_provider_protocol"},
            "unsupported provider_protocol",
        ),
        (
            "empty config path",
            {**base_payload, "config_path": " "},
            "config_path must be a non-empty string",
        ),
        (
            "url config path",
            {
                **base_payload,
                "config_path": "https://masked-status-loader-invalid.example/config.json",
            },
            "config_path must be a local filesystem path",
        ),
        (
            "file url config path",
            {
                **base_payload,
                "config_path": "file:///Users/example/private/config.json",
            },
            "config_path must be a local filesystem path",
        ),
        (
            "nul config path",
            {**base_payload, "config_path": "configs/local/masked-loader\x00secret.json"},
            "config_path must not contain control characters",
        ),
        (
            "del config path",
            {**base_payload, "config_path": "configs/local/masked-loader\x7fsecret.json"},
            "config_path must not contain control characters",
        ),
        (
            "posix traversal config path",
            {**base_payload, "config_path": "../configs/local/masked-loader-secret.json"},
            "config_path must not contain path traversal",
        ),
        (
            "windows traversal config path",
            {**base_payload, "config_path": r"configs\..\secret.json"},
            "config_path must not contain path traversal",
        ),
        (
            "secret reference not object",
            {**base_payload, "secret_reference": "masked-loader-secret-reference"},
            "secret_reference must be an object",
        ),
        (
            "secret reference extra field",
            {
                **base_payload,
                "secret_reference": {
                    **base_payload["secret_reference"],
                    "api_key": "TEST_MASKED_STATUS_LOADER_SECRET_REFERENCE_API_KEY",
                },
            },
            "secret_reference fields must match contract",
        ),
        (
            "non-string reference type",
            {
                **base_payload,
                "secret_reference": {
                    **base_payload["secret_reference"],
                    "reference_type": 123,
                },
            },
            "secret_reference reference_type must be a string",
        ),
        (
            "unsupported reference type",
            {
                **base_payload,
                "secret_reference": {
                    **base_payload["secret_reference"],
                    "reference_type": "plaintext_api_key",
                },
            },
            "unsupported secret_reference reference_type",
        ),
        (
            "empty reference id",
            {
                **base_payload,
                "secret_reference": {
                    **base_payload["secret_reference"],
                    "reference_id": " ",
                },
            },
            "secret_reference reference_id must be a non-empty string",
        ),
        (
            "control character reference id",
            {
                **base_payload,
                "secret_reference": {
                    **base_payload["secret_reference"],
                    "reference_id": "masked-loader-secret\x00reference",
                },
            },
            "secret_reference reference_id must not contain control characters",
        ),
        (
            "del character reference id",
            {
                **base_payload,
                "secret_reference": {
                    **base_payload["secret_reference"],
                    "reference_id": "masked-loader-secret\x7freference",
                },
            },
            "secret_reference reference_id must not contain control characters",
        ),
        (
            "display fields not list",
            {**base_payload, "requested_display_fields": "model"},
            "requested_display_fields must be a non-empty list",
        ),
        (
            "empty display fields",
            {**base_payload, "requested_display_fields": []},
            "requested_display_fields must be a non-empty list",
        ),
        (
            "non-string display field",
            {**base_payload, "requested_display_fields": ["model", 123]},
            "requested_display_fields values must be strings",
        ),
        (
            "unsupported display field",
            {**base_payload, "requested_display_fields": ["model", "raw_config"]},
            "unsupported requested_display_fields value",
        ),
        (
            "duplicate display field",
            {**base_payload, "requested_display_fields": ["model", "model"]},
            "requested_display_fields must not contain duplicates",
        ),
        (
            "authorization not object",
            {**base_payload, "authorization": "true"},
            "authorization must be an object",
        ),
        (
            "authorization missing status inference",
            {
                **base_payload,
                "authorization": {
                    key: value
                    for key, value in base_payload["authorization"].items()
                    if key != "allow_status_value_inference"
                },
            },
            "authorization fields must match dry run contract",
        ),
        (
            "allow config file read true",
            {
                **base_payload,
                "authorization": {
                    **base_payload["authorization"],
                    "allow_config_file_read": True,
                },
            },
            "allow_config_file_read must be false during dry run",
        ),
        (
            "allow secret read true",
            {
                **base_payload,
                "authorization": {
                    **base_payload["authorization"],
                    "allow_secret_read": True,
                },
            },
            "allow_secret_read must be false during dry run",
        ),
        (
            "allow llm call true",
            {
                **base_payload,
                "authorization": {
                    **base_payload["authorization"],
                    "allow_llm_call": True,
                },
            },
            "allow_llm_call must be false during dry run",
        ),
        (
            "allow event mutation true",
            {
                **base_payload,
                "authorization": {
                    **base_payload["authorization"],
                    "allow_event_mutation": True,
                },
            },
            "allow_event_mutation must be false during dry run",
        ),
        (
            "allow status inference true",
            {
                **base_payload,
                "authorization": {
                    **base_payload["authorization"],
                    "allow_status_value_inference": True,
                },
            },
            "allow_status_value_inference must be false during dry run",
        ),
        (
            "missing user confirmation",
            {
                **base_payload,
                "authorization": {
                    **base_payload["authorization"],
                    "user_confirmed_local_config_access": False,
                },
            },
            "user_confirmed_local_config_access must be true",
        ),
        (
            "missing storage policy acknowledgement value",
            {
                **base_payload,
                "authorization": {
                    **base_payload["authorization"],
                    "acknowledged_secret_storage_policy": False,
                },
            },
            "acknowledged_secret_storage_policy must be true",
        ),
    ]

    for case_name, payload, expected_detail in invalid_cases:
        response = client.post(
            "/live/asr/sessions/local_asr_provider_masked_status_loader_invalid/llm-provider-masked-status-loader-dry-run",
            json=payload,
        )

        assert response.status_code == 422, case_name
        assert response.json()["detail"] == expected_detail
        _assert_masked_status_loader_dry_run_response_redacts_submitted_values(
            response,
            sentinel_path,
            "masked-status-loader-invalid-secret-config.json",
            sentinel_reference,
            "TEST_MASKED_STATUS_LOADER_RAW_CONFIG_SECRET",
            "TEST_MASKED_STATUS_LOADER_EXTRA_API_KEY",
            "TEST_MASKED_STATUS_LOADER_EXTRA_BEARER",
            "TEST_MASKED_STATUS_LOADER_SECRET_REFERENCE_API_KEY",
            "masked-status-loader-invalid.example",
            "masked-loader-secret-reference",
        )


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


def test_web_mvp_readme_documents_scripted_browser_e2e_gate():
    root_readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    readme = (REPO_ROOT / "code" / "web_mvp" / "README.md").read_text(
        encoding="utf-8"
    )
    desktop_readme = (REPO_ROOT / "code" / "desktop_tauri" / "README.md").read_text(
        encoding="utf-8"
    )
    pcweb_076_plan = (
        REPO_ROOT
        / "docs"
        / "pcweb-076-live-asr-card-lifecycle-append-result-audit-event-persistence-preflight-plan.md"
    ).read_text(encoding="utf-8")
    pcweb_077_plan = (
        REPO_ROOT
        / "docs"
        / "pcweb-077-live-asr-card-lifecycle-readiness-summary-plan.md"
    ).read_text(encoding="utf-8")
    pcweb_078_plan = (
        REPO_ROOT
        / "docs"
        / "pcweb-078-live-asr-card-lifecycle-readiness-ui-plan.md"
    ).read_text(encoding="utf-8")
    pcweb_079_plan = (
        REPO_ROOT
        / "docs"
        / "pcweb-079-desktop-shell-readiness-boundary-plan.md"
    ).read_text(encoding="utf-8")
    pcweb_080_plan = (
        REPO_ROOT
        / "docs"
        / "pcweb-080-desktop-runtime-boundary-plan.md"
    ).read_text(encoding="utf-8")
    pcweb_080_implementation_plan = (
        REPO_ROOT
        / "docs"
        / "superpowers"
        / "plans"
        / "2026-07-02-pcweb-080-desktop-runtime-boundary.md"
    ).read_text(encoding="utf-8")
    pcweb_081_plan = (
        REPO_ROOT
        / "docs"
        / "pcweb-081-desktop-native-bridge-contract-plan.md"
    ).read_text(encoding="utf-8")
    pcweb_081_implementation_plan = (
        REPO_ROOT
        / "docs"
        / "superpowers"
        / "plans"
        / "2026-07-02-pcweb-081-desktop-native-bridge-contract.md"
    ).read_text(encoding="utf-8")
    pcweb_082_plan = (
        REPO_ROOT / "docs" / "pcweb-082-tauri-shell-scaffold-spike-plan.md"
    ).read_text(encoding="utf-8")
    pcweb_082_implementation_plan = (
        REPO_ROOT
        / "docs"
        / "superpowers"
        / "plans"
        / "2026-07-02-pcweb-082-tauri-shell-scaffold-spike.md"
    ).read_text(encoding="utf-8")
    pcweb_083_plan = (
        REPO_ROOT / "docs" / "pcweb-083-desktop-build-readiness-policy-plan.md"
    ).read_text(encoding="utf-8")
    pcweb_083_implementation_plan = (
        REPO_ROOT
        / "docs"
        / "superpowers"
        / "plans"
        / "2026-07-02-pcweb-083-desktop-build-readiness-policy.md"
    ).read_text(encoding="utf-8")
    pcweb_084_plan = (
        REPO_ROOT / "docs" / "pcweb-084-desktop-cargo-check-artifact-policy-plan.md"
    ).read_text(encoding="utf-8")
    pcweb_084_implementation_plan = (
        REPO_ROOT
        / "docs"
        / "superpowers"
        / "plans"
        / "2026-07-02-pcweb-084-desktop-cargo-check-artifact-policy.md"
    ).read_text(encoding="utf-8")
    pcweb_085_plan = (
        REPO_ROOT / "docs" / "pcweb-085-desktop-rust-toolchain-readiness-plan.md"
    ).read_text(encoding="utf-8")
    pcweb_085_implementation_plan = (
        REPO_ROOT
        / "docs"
        / "superpowers"
        / "plans"
        / "2026-07-02-pcweb-085-desktop-rust-toolchain-readiness.md"
    ).read_text(encoding="utf-8")
    pcweb_086_plan = (
        REPO_ROOT
        / "docs"
        / "pcweb-086-desktop-rust-toolchain-installation-decision-plan.md"
    ).read_text(encoding="utf-8")
    pcweb_086_implementation_plan = (
        REPO_ROOT
        / "docs"
        / "superpowers"
        / "plans"
        / "2026-07-02-pcweb-086-desktop-rust-toolchain-installation-decision.md"
    ).read_text(encoding="utf-8")
    pcweb_087_plan = (
        REPO_ROOT
        / "docs"
        / "pcweb-087-desktop-rust-toolchain-install-approval-packet-plan.md"
    ).read_text(encoding="utf-8")
    pcweb_087_implementation_plan = (
        REPO_ROOT
        / "docs"
        / "superpowers"
        / "plans"
        / "2026-07-02-pcweb-087-desktop-rust-toolchain-install-approval-packet.md"
    ).read_text(encoding="utf-8")
    pcweb_088_plan = (
        REPO_ROOT
        / "docs"
        / "pcweb-088-desktop-rust-post-install-probe-approval-plan.md"
    ).read_text(encoding="utf-8")
    pcweb_088_implementation_plan = (
        REPO_ROOT
        / "docs"
        / "superpowers"
        / "plans"
        / "2026-07-02-pcweb-088-desktop-rust-post-install-probe-approval.md"
    ).read_text(encoding="utf-8")
    pcweb_089_plan = (
        REPO_ROOT
        / "docs"
        / "pcweb-089-desktop-rust-post-install-probe-result-intake-plan.md"
    ).read_text(encoding="utf-8")
    pcweb_089_implementation_plan = (
        REPO_ROOT
        / "docs"
        / "superpowers"
        / "plans"
        / "2026-07-02-pcweb-089-desktop-rust-post-install-probe-result-intake.md"
    ).read_text(encoding="utf-8")
    pcweb_090_plan = (
        REPO_ROOT
        / "docs"
        / "pcweb-090-desktop-first-cargo-check-execution-boundary-plan.md"
    ).read_text(encoding="utf-8")
    pcweb_090_implementation_plan = (
        REPO_ROOT
        / "docs"
        / "superpowers"
        / "plans"
        / "2026-07-02-pcweb-090-desktop-first-cargo-check-execution-boundary.md"
    ).read_text(encoding="utf-8")
    pcweb_091_plan = (
        REPO_ROOT
        / "docs"
        / "pcweb-091-tauri-noop-shell-local-run-smoke-plan.md"
    ).read_text(encoding="utf-8")
    pcweb_091_implementation_plan = (
        REPO_ROOT
        / "docs"
        / "superpowers"
        / "plans"
        / "2026-07-02-pcweb-091-tauri-noop-shell-local-run-smoke.md"
    ).read_text(encoding="utf-8")
    requirements_traceability = (
        REPO_ROOT / "docs" / "requirements-traceability-matrix.md"
    ).read_text(encoding="utf-8")
    acceptance = (
        REPO_ROOT / "docs" / "pc-local-web-mvp-acceptance.md"
    ).read_text(encoding="utf-8")
    privacy = (REPO_ROOT / "docs" / "privacy-and-data-flow.md").read_text(
        encoding="utf-8"
    )
    project_structure = (REPO_ROOT / "docs" / "project-structure.md").read_text(
        encoding="utf-8"
    )
    roadmap = (REPO_ROOT / "docs" / "implementation-roadmap.md").read_text(
        encoding="utf-8"
    )
    decision_log = (REPO_ROOT / "docs" / "decision-log.md").read_text(
        encoding="utf-8"
    )
    project_progress = (
        REPO_ROOT / "docs" / "project-progress-report-2026-07-02.md"
    ).read_text(encoding="utf-8")
    project_current_status = (
        REPO_ROOT / "docs" / "project-current-status-2026-07-02.md"
    ).read_text(encoding="utf-8")
    project_stage_status = (
        REPO_ROOT / "docs" / "project-stage-status-and-next-work-2026-07-02.md"
    ).read_text(encoding="utf-8")

    assert "node e2e/browser_smoke.mjs" in readme
    assert "MEETING_COPILOT_DATA_DIR" in readme
    assert "evidence click-back" in readme
    assert "schema degradation" in readme
    assert "GET /live/asr/sessions/{session_id}/suggestion-candidates" in readme
    assert "GET /live/asr/sessions/{session_id}/llm-request-drafts" in readme
    assert "GET /live/asr/sessions/{session_id}/llm-openai-request-body-previews" in readme
    assert "local_sensitive_draft_value_guard.v1" in readme
    assert "PCWEB-062" in readme
    assert "schema_outline_status=outline_only" in readme
    assert "additional_properties_status=allowed_by_local_contract_extra" in readme
    assert "PCWEB-063" in readme
    assert "POST /live/asr/sessions/{session_id}/llm-schema-validation-dry-runs" in readme
    assert "PCWEB-064" in readme
    assert "POST /live/asr/sessions/{session_id}/llm-card-creation-policy-dry-runs" in readme
    assert "PCWEB-065" in readme
    assert "POST /live/asr/sessions/{session_id}/llm-card-lifecycle-preview-dry-runs" in readme
    assert "PCWEB-066" in readme
    assert (
        "POST /live/asr/sessions/{session_id}/llm-card-lifecycle-append-preflight-dry-runs"
        in readme
    )
    assert "PCWEB-067" in readme
    assert "POST /live/asr/sessions/{session_id}/llm-card-lifecycle-append-runs" in readme
    assert "PCWEB-068" in readme
    assert (
        "POST /live/asr/sessions/{session_id}/llm-card-lifecycle-append-repository-dry-runs"
        in readme
    )
    assert "repository_write_status=dry_run_only" in readme
    assert "no repository transaction" in readme
    assert "PCWEB-069" in readme
    assert (
        "POST /live/asr/sessions/{session_id}/llm-card-lifecycle-append-transaction-runs"
        in readme
    )
    assert "transaction_write_status=disabled" in readme
    assert "no repository transaction commit" in readme
    assert "PCWEB-070" in readme
    assert (
        "POST /live/asr/sessions/{session_id}/llm-card-lifecycle-append-result-audit-previews"
        in readme
    )
    assert "append_result_audit_event_status=preview_only" in readme
    assert "no audit event append" in readme
    assert "PCWEB-071" in readme
    assert (
        "POST /live/asr/sessions/{session_id}/llm-card-lifecycle-retry-replay-preflights"
        in readme
    )
    assert "retry_replay_preflight_status=analyzed" in readme
    assert "PCWEB-072" in readme
    assert (
        "POST /live/asr/sessions/{session_id}/llm-card-lifecycle-append-event-serializer-dry-runs"
        in readme
    )
    assert "append_event_serializer_status=serialized" in readme
    assert "event_append_status=not_appended" in readme
    assert "idempotency_store_write_status=not_written" in readme
    assert "no event mutation" in readme
    assert "PCWEB-073" in readme
    assert (
        "POST /live/asr/sessions/{session_id}/llm-card-lifecycle-append-mutation-preflights"
        in readme
    )
    assert "append_mutation_preflight_status=analyzed" in readme
    assert "safe_to_mutate_events=false" in readme
    assert "PCWEB-074" in readme
    assert (
        "POST /live/asr/sessions/{session_id}/llm-card-lifecycle-append-transaction-commit-preflights"
        in readme
    )
    assert "append_transaction_commit_preflight_status=analyzed" in readme
    assert "safe_to_commit_transaction=false" in readme
    assert "PCWEB-075" in readme
    assert (
        "POST /live/asr/sessions/{session_id}/llm-card-lifecycle-append-idempotency-store-write-preflights"
        in readme
    )
    assert "idempotency_store_write_preflight_status=analyzed" in readme
    assert "safe_to_write_idempotency_store=false" in readme
    assert "no idempotency-store write or marker" in readme
    assert "no repository transaction begin/commit/rollback" in readme
    assert "PCWEB-076" in readme
    assert (
        "POST /live/asr/sessions/{session_id}/llm-card-lifecycle-append-result-audit-event-persistence-preflights"
        in readme
    )
    assert "append_result_audit_event_persistence_preflight_status=analyzed" in readme
    assert "safe_to_persist_append_result_audit_event=false" in readme
    assert "no audit event append" in readme
    assert "PCWEB-077" in readme
    assert (
        "POST /live/asr/sessions/{session_id}/llm-card-lifecycle-readiness-summaries"
        in readme
    )
    assert "card_lifecycle_readiness_summary_status=summarized" in readme
    assert "card_lifecycle_safe_to_execute_llm=false" in readme
    assert "no event mutation" in readme
    assert "PCWEB-078" in readme
    assert "card-lifecycle-readiness-panel" in readme
    assert "Live ASR terminal summary" in readme
    assert "PCWEB-079" in readme
    assert "GET /desktop/shell-readiness" in readme
    assert "desktop-readiness-panel" in readme
    assert "desktop_safe_to_capture_audio=false" in readme
    assert "PCWEB-080" in readme
    assert "GET /desktop/runtime-boundary" in readme
    assert "desktop-runtime-boundary-panel" in readme
    assert "desktop_runtime_safe_to_create_shell=false" in readme
    assert "loads desktop readiness, desktop runtime boundary, and the fixture list" in readme
    assert "PCWEB-081" in readme
    assert "GET /desktop/native-bridge-contract" in readme
    assert "desktop-native-bridge-contract-panel" in readme
    assert "desktop_bridge_safe_to_create_native_bridge=false" in readme
    assert "PCWEB-082" in readme
    assert "code/desktop_tauri" in readme
    assert "src-tauri" in readme
    assert "tauri.conf.json" in readme
    assert "runtime_get_status" in readme
    assert "session_prepare" in readme
    assert "asr_worker_health" in readme
    assert "noop_bound" in readme
    assert "safe_to_execute_real_action=false" in readme
    for document in [
        pcweb_076_plan,
        requirements_traceability,
        acceptance,
        privacy,
        roadmap,
    ]:
        assert "PCWEB-076" in document
        assert (
            "llm-card-lifecycle-append-result-audit-event-persistence-preflights"
            in document
        )
    assert "transaction_run_id" in pcweb_076_plan
    assert "append_run_id" in pcweb_076_plan
    assert "repository_result_id" in pcweb_076_plan
    assert "audit_repository_transaction_status" in pcweb_076_plan
    assert "safe_to_mutate_events" in pcweb_076_plan
    assert "safe_to_append_events" in pcweb_076_plan
    for document in [
        pcweb_077_plan,
        requirements_traceability,
        acceptance,
        privacy,
        project_structure,
        roadmap,
        decision_log,
    ]:
        assert "PCWEB-077" in document
        assert "llm-card-lifecycle-readiness-summaries" in document
    assert "card_lifecycle_summary_phase_count" in pcweb_077_plan
    assert "source_preflight_status" in pcweb_077_plan
    assert "card_lifecycle_safe_to_execute_llm" in pcweb_077_plan
    assert "must not copy unscoped upstream `safe_to_*` fields" in pcweb_077_plan
    for document in [
        pcweb_078_plan,
        requirements_traceability,
        acceptance,
        privacy,
        project_structure,
        roadmap,
        decision_log,
    ]:
        assert "PCWEB-078" in document
        assert "llm-card-lifecycle-readiness-summaries" in document
    assert "card-lifecycle-readiness-panel" in pcweb_078_plan
    assert "local contract probe" in pcweb_078_plan
    assert "No real LLM call" in pcweb_078_plan
    for document in [
        pcweb_079_plan,
        requirements_traceability,
        acceptance,
        privacy,
        project_structure,
        roadmap,
        decision_log,
    ]:
        assert "PCWEB-079" in document
        assert "/desktop/shell-readiness" in document
    assert "desktop-readiness-panel" in pcweb_079_plan
    assert "No microphone capture" in pcweb_079_plan
    assert "desktop_safe_to_capture_audio" in pcweb_079_plan
    for document in [
        pcweb_080_plan,
        pcweb_080_implementation_plan,
        requirements_traceability,
        acceptance,
        privacy,
        project_structure,
        roadmap,
        decision_log,
    ]:
        assert "PCWEB-080" in document
        assert "/desktop/runtime-boundary" in document
    assert "tauri_first_electron_fallback" in pcweb_080_plan
    assert "sidecar_worker_planned" in pcweb_080_plan
    assert "desktop_runtime_safe_to_create_shell" in pcweb_080_plan
    assert "Implementation status: completed" in pcweb_080_implementation_plan
    assert "启动时被动加载 readiness、runtime boundary 和 demo fixture 列表" in requirements_traceability
    assert "只读取 readiness、runtime boundary 和 fixture 列表" in privacy
    assert "PCWEB-080 extends this passive startup read set" in decision_log
    for document in [
        pcweb_081_plan,
        pcweb_081_implementation_plan,
        requirements_traceability,
        acceptance,
        privacy,
        project_structure,
        roadmap,
        decision_log,
    ]:
        assert "PCWEB-081" in document
        assert "/desktop/native-bridge-contract" in document
    assert "runtime.get_status" in pcweb_081_plan
    assert "audio.capture_start" in pcweb_081_plan
    assert "asr_worker.start" in pcweb_081_plan
    assert "desktop_bridge_safe_to_create_native_bridge" in pcweb_081_plan
    assert "create_tauri_shell_scaffold_against_bridge_contract" in pcweb_081_plan
    assert "create_tauri_shell_spike" not in pcweb_081_plan
    for document in [
        root_readme,
        readme,
        pcweb_082_plan,
        pcweb_082_implementation_plan,
        requirements_traceability,
        acceptance,
        privacy,
        project_structure,
        roadmap,
        decision_log,
    ]:
        assert "PCWEB-082" in document
        assert "code/desktop_tauri" in document
        assert "src-tauri" in document
        assert "tauri.conf.json" in document
    for document in [
        root_readme,
        readme,
        pcweb_082_plan,
        pcweb_082_implementation_plan,
        requirements_traceability,
        acceptance,
        privacy,
        project_structure,
        roadmap,
        decision_log,
    ]:
        assert "runtime_get_status" in document
        assert "session_prepare" in document
        assert "asr_worker_health" in document
        assert "safe_to_execute_real_action=false" in document
    assert "devUrl" in pcweb_082_plan
    assert "frontendDist" in pcweb_082_plan
    assert "http://127.0.0.1:8765/" in pcweb_082_plan
    assert "Cargo.lock" in pcweb_082_plan
    assert "package.json" in pcweb_082_plan
    assert "Implementation status: completed" in pcweb_082_implementation_plan
    assert "root-pytest" in root_readme
    assert "root scaffold contract tests" in root_readme
    assert "Cargo.lock" in root_readme
    assert "package.json" in root_readme
    for document in [
        root_readme,
        readme,
        pcweb_083_plan,
        pcweb_083_implementation_plan,
        requirements_traceability,
        acceptance,
        privacy,
        project_structure,
        roadmap,
        decision_log,
    ]:
        assert "PCWEB-083" in document
        assert "build-readiness.policy.json" in document
        assert "desktop_build_readiness.py" in document
        assert "safe_to_run_cargo_check_now=false" in document
        assert "toolchain_version_probe_only" in document
    for document in [
        root_readme,
        readme,
        pcweb_084_plan,
        pcweb_084_implementation_plan,
        requirements_traceability,
        acceptance,
        privacy,
        project_structure,
        roadmap,
        decision_log,
    ]:
        assert "PCWEB-084" in document
        assert "cargo-check.policy.json" in document
        assert "desktop_cargo_check_policy.py" in document
        assert "CARGO_TARGET_DIR=artifacts/tmp/desktop_tauri_target" in document
        assert "safe_to_run_cargo_check_now=false" in document
    assert "blocked_until_explicit_approval_and_toolchain" in pcweb_084_plan
    assert "blocked_until_cargo_lock_and_cache_exist" in pcweb_084_plan
    assert "Implementation Status" in pcweb_084_implementation_plan
    for document in [
        root_readme,
        readme,
        pcweb_085_plan,
        pcweb_085_implementation_plan,
        requirements_traceability,
        acceptance,
        privacy,
        project_structure,
        roadmap,
        decision_log,
    ]:
        assert "PCWEB-085" in document
        assert "rust-toolchain-readiness.policy.json" in document
        assert "desktop_rust_toolchain_readiness.py" in document
        assert "local_version_and_platform_probe_only" in document
        assert "safe_to_install_toolchain_now=false" in document
    assert "xcode-select -p" in pcweb_085_plan
    assert "presence_only" in pcweb_085_plan
    for document in [
        root_readme,
        readme,
        pcweb_086_plan,
        pcweb_086_implementation_plan,
        requirements_traceability,
        acceptance,
        privacy,
        project_structure,
        roadmap,
        decision_log,
    ]:
        assert "PCWEB-086" in document
        assert "rust-toolchain-installation.policy.json" in document
        assert "desktop_rust_toolchain_installation_decision.py" in document
        assert "no_install_decision_report_only" in document
        assert "safe_to_install_toolchain_now=false" in document
    assert "official_rustup" in pcweb_086_plan
    assert "approved_network_download_policy_for_rustup" in pcweb_086_plan
    for document in [
        root_readme,
        readme,
        pcweb_087_plan,
        pcweb_087_implementation_plan,
        requirements_traceability,
        acceptance,
        privacy,
        project_structure,
        roadmap,
        decision_log,
    ]:
        assert "PCWEB-087" in document
        assert "rust-toolchain-install-approval.policy.json" in document
        assert "desktop_rust_toolchain_install_approval_packet.py" in document
        assert "manual_user_run_only" in document
        assert "safe_to_execute_install_now=false" in document
    assert "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh" in pcweb_087_plan
    assert "rustup-init.exe" in pcweb_087_plan
    assert "approved_manual_user_run_only_boundary" in pcweb_087_plan
    assert "manual_rustup_self_uninstall_only" in pcweb_087_plan
    for document in [
        root_readme,
        readme,
        desktop_readme,
        pcweb_088_plan,
        pcweb_088_implementation_plan,
        requirements_traceability,
        acceptance,
        privacy,
        project_structure,
        roadmap,
        decision_log,
    ]:
        assert "PCWEB-088" in document
        assert "rust-post-install-probe-approval.policy.json" in document
        assert "desktop_rust_post_install_probe_approval.py" in document
        assert "no_probe_execution_approval_packet_only" in document
        assert "safe_to_run_post_install_probe_now=false" in document
        assert "safe_to_run_cargo_check_now=false" in document
    assert "rustc --version" in pcweb_088_plan
    assert "cargo --version" in pcweb_088_plan
    assert "rustup --version" in pcweb_088_plan
    assert "xcode-select -p" in pcweb_088_plan
    assert "presence_only_no_path" in pcweb_088_plan
    for document in [
        root_readme,
        readme,
        desktop_readme,
        pcweb_089_plan,
        pcweb_089_implementation_plan,
        requirements_traceability,
        acceptance,
        privacy,
        project_structure,
        roadmap,
        decision_log,
        project_progress,
        project_current_status,
    ]:
        assert "PCWEB-089" in document
        assert "rust-post-install-probe-result-intake.policy.json" in document
        assert "desktop_rust_post_install_probe_result_intake.py" in document
        assert "manual_result_validation_only" in document
        assert "caller_provided_json_only" in document
        assert "safe_to_accept_raw_probe_output_now=false" in document
        assert "safe_to_run_cargo_check_now=false" in document
        assert "blocked_until_pcweb_084_and_user_approval" in document
    for document in [
        root_readme,
        readme,
        desktop_readme,
        pcweb_090_plan,
        pcweb_090_implementation_plan,
        requirements_traceability,
        acceptance,
        privacy,
        project_structure,
        roadmap,
        decision_log,
        project_progress,
        project_current_status,
    ]:
        assert "PCWEB-090" in document
        assert "first-cargo-check-execution.policy.json" in document
        assert "desktop_first_cargo_check_execution_boundary.py" in document
        assert "explicit_manual_execution_packet_only" in document
        assert "ready_for_explicit_user_approval" in document
        assert "safe_to_run_cargo_check_now=false" in document
        assert "CARGO_TARGET_DIR=artifacts/tmp/desktop_tauri_target" in document
    for document in [
        root_readme,
        readme,
        desktop_readme,
        pcweb_091_plan,
        pcweb_091_implementation_plan,
        requirements_traceability,
        acceptance,
        privacy,
        project_structure,
        roadmap,
        decision_log,
        project_progress,
        project_current_status,
        project_stage_status,
    ]:
        assert "PCWEB-091" in document
        assert "tauri-noop-shell-run-smoke.policy.json" in document
        assert "desktop_tauri_noop_shell_run_smoke.py" in document
        assert "readiness_report_only" in document
        assert "ready_for_explicit_tauri_run_approval" in document
        assert "safe_to_run_tauri_dev_now=false" in document
        assert "safe_to_capture_audio_now=false" in document
    assert "GET /live/asr/sessions/{session_id}/llm-execution-previews" in readme
    assert "POST /live/asr/sessions/{session_id}/llm-execution-runs" in readme
    assert "GET /live/asr/sessions/{session_id}/llm-provider-readiness" in readme
    assert "GET /live/asr/sessions/{session_id}/llm-provider-config-boundary" in readme
    assert "GET /live/asr/sessions/{session_id}/llm-provider-masked-status" in readme
    assert "POST /live/asr/sessions/{session_id}/llm-provider-config-validation" in readme
    assert "POST /live/asr/sessions/{session_id}/llm-provider-config-loader-preflight" in readme
    assert "GET /live/asr/sessions/{session_id}/llm-provider-secret-storage-policy" in readme
    assert "POST /live/asr/sessions/{session_id}/llm-provider-config-reader-dry-run" in readme
    assert "POST /live/asr/sessions/{session_id}/llm-provider-masked-status-loader-dry-run" in readme
    assert "PCWEB-057" in readme
    assert "PCWEB-058" in readme
    assert "PCWEB-059" in readme
    assert "PCWEB-060" in readme
    assert "PCWEB-061" in readme
    assert "PCWEB-063" in readme
    assert "PCWEB-064" in readme
    assert "PCWEB-065" in readme
    assert "PCWEB-066" in readme
    assert "PCWEB-067" in readme
    assert "PCWEB-068" in readme
    assert "PCWEB-069" in readme
    assert "PCWEB-071" in readme
    assert "PCWEB-072" in readme
    assert "PCWEB-073" in readme


def test_scripted_browser_e2e_gate_exists_and_checks_critical_ui_paths():
    script = REPO_ROOT / "code" / "web_mvp" / "e2e" / "browser_smoke.mjs"

    assert script.is_file()
    text = script.read_text(encoding="utf-8")
    assert "Google Chrome" in text
    assert "json_smoke_api_review" in text
    assert "schema-degradation-review" in text
    assert "evidence-ev_002" in text
    assert "transcript-segment-seg_002" in text
    assert "suggestion_silenced" in text
    assert "desktop-readiness-panel" in text
    assert "/desktop/shell-readiness" in text
    assert "desktop-runtime-boundary-panel" in text
    assert "/desktop/runtime-boundary" in text
    assert "desktop-native-bridge-contract-panel" in text
    assert "/desktop/native-bridge-contract" in text
    assert "card-lifecycle-readiness-panel" in text
    assert "llm-card-lifecycle-readiness-summaries" in text
    assert "lifecycle-phase" in text
    assert "cdpSockets" in text
    assert "SIGKILL" in text


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
