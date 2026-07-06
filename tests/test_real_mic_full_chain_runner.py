import importlib.util
import json
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "real_mic_full_chain_runner.py"


def load_tool_module():
    spec = importlib.util.spec_from_file_location(
        "real_mic_full_chain_runner",
        TOOL_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_real_mic_runner_writes_traceable_artifacts_without_remote_calls(tmp_path):
    tool = load_tool_module()

    def fake_recorder(*, audio_path, record_seconds, audio_device_index):
        audio_path.parent.mkdir(parents=True, exist_ok=True)
        audio_path.write_bytes(b"RIFFfake-wav")
        return {
            "capture_status": "recorded_from_real_microphone",
            "audio_path": str(audio_path),
            "record_seconds": record_seconds,
            "elapsed_ms": 1250,
            "audio_device_index": audio_device_index,
            "audio_file_size_bytes": audio_path.stat().st_size,
        }

    def fake_transcriber(*, audio_path, events_output, model_dir, chunk_ms, num_threads):
        events = [
            {
                "event_type": "final",
                "segment_id": "real_mic_seg_001",
                "text": "payment gateway 灰度百分之五，P99 超过九百毫秒就回滚。",
                "start_ms": 0,
                "end_ms": 4200,
                "received_at_ms": 4500,
                "confidence": 0.87,
            },
            {
                "event_type": "end_of_stream",
                "segment_id": "real_mic_eos",
                "text": "",
                "start_ms": 4200,
                "end_ms": 4200,
                "received_at_ms": 4500,
            },
        ]
        events_output.parent.mkdir(parents=True, exist_ok=True)
        events_output.write_text(json.dumps(events, ensure_ascii=False), encoding="utf-8")
        return {
            "status": "ok",
            "text": "payment gateway 灰度百分之五，P99 超过九百毫秒就回滚。",
            "latency_ms": 830,
            "audio_duration_seconds": 4.2,
            "rtf": 0.197619,
            "segments": [
                {
                    "id": "real_mic_seg_001",
                    "start_ms": 0,
                    "end_ms": 4200,
                    "text": "payment gateway 灰度百分之五，P99 超过九百毫秒就回滚。",
                    "confidence": 0.87,
                    "is_final": True,
                }
            ],
            "raw": {
                "provider": "sherpa-onnx",
                "partial_event_count": 0,
                "final_event_count": 1,
                "revision_event_count": 0,
                "error_event_count": 0,
                "end_of_stream_event_count": 1,
            },
        }

    def fake_web_handoff(*, session_id, provider, events_path, data_dir, repo_root):
        events = json.loads(events_path.read_text(encoding="utf-8"))
        return {
            "handoff_status": "web_live_asr_ingested",
            "status_code": 201,
            "create_response": {
                "session_id": session_id,
                "ingest_mode": "local_asr_event_file",
                "event_source": {"provider": provider},
                "live_event_counts": {
                    "transcript_final": 1,
                    "state_event": 1,
                    "scheduler_event": 1,
                    "suggestion_candidate_event": 1,
                    "llm_request_draft_event": 1,
                },
                "live_events": [
                    {
                        "event_type": "transcript_final",
                        "payload": {
                            "segment_id": "real_mic_seg_001",
                            "text": events[0]["text"],
                            "start_ms": 0,
                            "end_ms": 4200,
                            "evidence_spans": [
                                {
                                    "id": "asr_ev_real_mic_seg_001",
                                    "segment_id": "real_mic_seg_001",
                                    "start_ms": 0,
                                    "end_ms": 4200,
                                    "quote": events[0]["text"],
                                    "status": "active",
                                }
                            ],
                        },
                    },
                    {
                        "event_type": "state_event",
                        "payload": {
                            "event_id": "asr_state_event_real_mic_seg_001",
                            "target_type": "DecisionCandidate",
                            "evidence_span_ids": ["asr_ev_real_mic_seg_001"],
                        },
                    },
                    {
                        "event_type": "suggestion_candidate_event",
                        "payload": {
                            "target_candidate_id": "asr_decision_real_mic_seg_001",
                            "candidate_type": "decision_review",
                            "candidate_text": "确认 payment gateway 灰度回滚阈值和 owner。",
                            "evidence_span_ids": ["asr_ev_real_mic_seg_001"],
                            "created_at_ms": 4500,
                            "candidate_latency_ms": 300,
                        },
                    },
                ],
            },
            "draft": {
                "session_id": session_id,
                "transcript": {"segment_count": 1},
            },
            "draft_markdown": "# Real Mic Draft\n\n- payment gateway 灰度百分之五",
            "suggestion_candidates": {"candidate_count": 1},
        }

    result = tool.run_real_mic_full_chain(
        session_id="real_mic_unit",
        record_seconds=4,
        repo_root=tmp_path,
        recorder=fake_recorder,
        transcriber=fake_transcriber,
        web_handoff=fake_web_handoff,
    )

    assert result["runner_status"] == "main_flow_passed"
    assert result["privacy_cost_flags"] == {
        "raw_audio_uploaded": False,
        "remote_asr_called": False,
        "llm_called": False,
        "configs_local_read": False,
        "user_audio_committed_to_repo": False,
    }
    assert result["capture"]["audio_path"] == (
        "artifacts/tmp/desktop_mic_adapter_runtime/audio_chunks/real_mic_unit/audio.wav"
    )
    assert result["asr"]["events_path"] == "artifacts/tmp/asr_events/real_mic_unit.sherpa.events.json"
    assert result["web_handoff"]["handoff_status"] == "web_live_asr_ingested"
    assert result["report"]["candidate_report_validation_status"] == "passed"
    assert Path(result["artifacts"]["summary_path"]).is_file()
    assert Path(result["artifacts"]["candidate_report_path"]).is_file()
    assert Path(result["artifacts"]["draft_markdown_path"]).is_file()


def test_real_mic_runner_rejects_unsafe_session_ids(tmp_path):
    tool = load_tool_module()

    result = tool.run_real_mic_full_chain(
        session_id="../configs/local/leak",
        record_seconds=1,
        repo_root=tmp_path,
        recorder=lambda **_: {},
        transcriber=lambda **_: {},
        web_handoff=lambda **_: {},
    )

    assert result["runner_status"] == "blocked_by_session_id_validation"
    assert result["validation_errors"] == [
        "session_id must contain only letters, numbers, underscore, or hyphen",
    ]


def test_recorder_reports_timeout_instead_of_hanging(monkeypatch, tmp_path):
    tool = load_tool_module()

    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(tool.subprocess, "run", fake_run)
    monkeypatch.setattr(tool.time, "sleep", lambda _seconds: None)

    result = tool.record_microphone_to_wav(
        audio_path=tmp_path / "audio.wav",
        record_seconds=4,
        audio_device_index=0,
    )

    assert result["capture_status"] == "blocked_by_microphone_capture_timeout"
    assert result["timeout_seconds"] == 14


def test_runner_clamps_file_replay_received_timestamps_for_web_handoff(tmp_path):
    tool = load_tool_module()

    def fake_recorder(*, audio_path, record_seconds, audio_device_index):
        audio_path.parent.mkdir(parents=True, exist_ok=True)
        audio_path.write_bytes(b"RIFFfake-wav")
        return {
            "capture_status": "recorded_from_real_microphone",
            "record_seconds": record_seconds,
            "elapsed_ms": 1000,
            "audio_device_index": audio_device_index,
            "audio_file_size_bytes": audio_path.stat().st_size,
        }

    def fake_transcriber(*, audio_path, events_output, model_dir, chunk_ms, num_threads):
        events = [
            {
                "event_type": "final",
                "segment_id": "replay_seg_001",
                "text": "payment-gateway 本周四灰度百分之五。",
                "start_ms": 0,
                "end_ms": 4000,
                "received_at_ms": 200,
                "confidence": 0.8,
            },
            {
                "event_type": "end_of_stream",
                "segment_id": "replay_eos",
                "text": "",
                "start_ms": 4000,
                "end_ms": 4000,
                "received_at_ms": 210,
            },
        ]
        events_output.parent.mkdir(parents=True, exist_ok=True)
        events_output.write_text(json.dumps(events, ensure_ascii=False), encoding="utf-8")
        return {
            "status": "ok",
            "text": "payment-gateway 本周四灰度百分之五。",
            "latency_ms": 200,
            "audio_duration_seconds": 4.0,
            "rtf": 0.05,
            "segments": [
                {
                    "id": "replay_seg_001",
                    "start_ms": 0,
                    "end_ms": 4000,
                    "text": "payment-gateway 本周四灰度百分之五。",
                    "confidence": 0.8,
                    "is_final": True,
                }
            ],
            "raw": {
                "provider": "sherpa-onnx",
                "final_event_count": 1,
                "end_of_stream_event_count": 1,
            },
        }

    def fake_web_handoff(*, session_id, provider, events_path, data_dir, repo_root):
        assert events_path.name == "real_mic_replay_unit.web.events.json"
        events = json.loads(events_path.read_text(encoding="utf-8"))
        assert all(event["received_at_ms"] >= event["end_ms"] for event in events)
        return {
            "handoff_status": "web_live_asr_ingested",
            "status_code": 201,
            "create_response": {
                "session_id": session_id,
                "ingest_mode": "local_asr_event_file",
                "event_source": {"provider": provider},
                "live_event_counts": {
                    "transcript_final": 1,
                    "state_event": 1,
                    "scheduler_event": 1,
                    "suggestion_candidate_event": 1,
                    "llm_request_draft_event": 1,
                },
                "live_events": [
                    {
                        "event_type": "transcript_final",
                        "payload": {
                            "segment_id": "replay_seg_001",
                            "text": "payment-gateway 本周四灰度百分之五。",
                            "start_ms": 0,
                            "end_ms": 4000,
                            "evidence_spans": [
                                {
                                    "id": "asr_ev_replay_seg_001",
                                    "segment_id": "replay_seg_001",
                                    "start_ms": 0,
                                    "end_ms": 4000,
                                    "quote": "payment-gateway 本周四灰度百分之五。",
                                    "status": "active",
                                }
                            ],
                        },
                    },
                    {
                        "event_type": "state_event",
                        "payload": {
                            "event_id": "asr_state_event_replay_seg_001",
                            "target_type": "DecisionCandidate",
                            "evidence_span_ids": ["asr_ev_replay_seg_001"],
                        },
                    },
                    {
                        "event_type": "suggestion_candidate_event",
                        "payload": {
                            "candidate_id": "asr_suggestion_candidate_replay_seg_001",
                            "candidate_type": "state_gap_review",
                            "suggested_prompt": "确认灰度 owner 和回滚阈值。",
                            "evidence_span_ids": ["asr_ev_replay_seg_001"],
                        },
                    },
                ],
            },
            "draft": {"session_id": session_id},
            "draft_markdown": "# Draft",
            "suggestion_candidates": {"candidate_count": 1},
        }

    result = tool.run_real_mic_full_chain(
        session_id="real_mic_replay_unit",
        record_seconds=4,
        repo_root=tmp_path,
        recorder=fake_recorder,
        transcriber=fake_transcriber,
        web_handoff=fake_web_handoff,
    )

    assert result["runner_status"] == "main_flow_passed"
    assert result["asr"]["events_path"] == "artifacts/tmp/asr_events/real_mic_replay_unit.sherpa.events.json"
    assert result["asr"]["web_events_path"] == "artifacts/tmp/asr_events/real_mic_replay_unit.web.events.json"
