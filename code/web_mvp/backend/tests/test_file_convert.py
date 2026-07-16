"""Tests for meeting-recording file conversion (G1)."""
import hashlib
import json
from io import BytesIO
from pathlib import Path
import subprocess
import shutil
import sys
from fastapi.testclient import TestClient
from meeting_copilot_web_mvp import asr_correct, batch_transcribe, llm_service
from meeting_copilot_web_mvp.app import create_app


def _offline_asr_report(text: str) -> dict:
    return {
        "text": text,
        "raw": {
            "provider": "funasr",
            "mode": "file_batch_offline_transcript",
            "punc_model_status": "enabled",
        },
        "audio_duration_seconds": 12.5,
        "rtf": 0.05,
        "batch": {
            "batch_mode": "single_process_reused_funasr_offline_model",
            "transcribe_only_rtf": 0.05,
        },
    }


def test_get_ffmpeg_falls_back_to_system_binary_when_imageio_is_unavailable(monkeypatch):
    monkeypatch.setattr(batch_transcribe, "_ffmpeg_path", None)
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", None)
    monkeypatch.setattr(batch_transcribe, "shutil", shutil, raising=False)
    monkeypatch.setattr(
        batch_transcribe.shutil,
        "which",
        lambda name: "/opt/homebrew/bin/ffmpeg" if name == "ffmpeg" else None,
    )

    assert batch_transcribe._get_ffmpeg() == "/opt/homebrew/bin/ffmpeg"


def test_transcribe_file_session_creates_session_from_audio(monkeypatch):
    # mock the slow FunASR batch subprocess
    monkeypatch.setattr(batch_transcribe, "transcribe_file_report", lambda p: _offline_asr_report("先灰度 5%。谁负责回滚？"))
    monkeypatch.setattr(batch_transcribe, "is_available", lambda: True)

    client = TestClient(create_app())
    r = client.post(
        "/live/asr/transcribe-file/sessions",
        files={"file": ("meeting.wav", BytesIO(b"fake-audio"), "audio/wav")},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["provider"] == "local_funasr_batch"
    assert "灰度" in body["transcript"]
    sid = body["session_id"]
    assert sid.startswith("file_")

    # session queryable
    ev = client.get(f"/live/asr/sessions/{sid}/events")
    assert ev.status_code == 200
    event_body = ev.json()
    assert event_body["events"], "no events persisted from file conversion"
    profile = event_body["event_source"]["post_meeting_asr_profile"]
    assert profile["mode"] == "file_batch_offline_transcript"
    assert profile["punc_model_status"] == "enabled"
    assert profile["batch_mode"] == "single_process_reused_funasr_offline_model"
    assert profile["rtf"] == 0.05

    # llm-execution-runs works on the file-converted session (mock LLM)
    class FakeClient:
        def post_json(self, url, headers, body, timeout):
            return {"choices": [{"message": {"content": '{"suggestion_text":"建议确认 owner","confidence":0.8,"trigger_reason":"owner 缺失"}'}}], "usage": {"total_tokens": 50}}

    monkeypatch.setattr(llm_service, "HttpxLlmClient", lambda: FakeClient())
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gw.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "m1")
    cards = client.post(f"/live/asr/sessions/{sid}/llm-execution-runs", json={"mode": "enabled"})
    assert cards.status_code == 200
    assert cards.json()["run_count"] >= 1


def test_file_converted_session_exports_transcript_and_minutes(monkeypatch):
    monkeypatch.setattr(
        batch_transcribe,
        "transcribe_file_report",
        lambda p: _offline_asr_report("接口先灰度 5%，错误率超过 0.1% 就回滚，owner 张三今天补 SLO 看板。"),
    )
    monkeypatch.setattr(batch_transcribe, "is_available", lambda: True)
    monkeypatch.setattr(asr_correct, "correct_transcript", lambda raw, cfg: (raw, {"total_tokens": 0}, False))
    monkeypatch.setattr(
        llm_service.LlmConfig,
        "from_env",
        lambda: llm_service.LlmConfig(base_url="https://gw.example", api_key="sk-test", model="m1"),
    )
    monkeypatch.setattr(
        llm_service,
        "build_minutes",
        lambda transcript, config: ("# 会议纪要\n\n- 先灰度 5%\n", {"total_tokens": 12}, False),
    )

    client = TestClient(create_app())
    created = client.post(
        "/live/asr/transcribe-file/sessions",
        files={"file": ("meeting.wav", BytesIO(b"fake-audio"), "audio/wav")},
    )
    assert created.status_code == 201, created.text
    sid = created.json()["session_id"]

    transcript = client.get(f"/live/asr/sessions/{sid}/transcript.txt")
    assert transcript.status_code == 200
    assert "text/plain" in transcript.headers["content-type"]
    assert f"{sid}.transcript.txt" in transcript.headers["content-disposition"]
    assert "接口先灰度" in transcript.text

    created_minutes = client.post(f"/live/asr/sessions/{sid}/minutes", json={"mode": "enabled"})
    assert created_minutes.status_code == 200, created_minutes.text
    minutes = client.get(f"/live/asr/sessions/{sid}/minutes.md")
    assert minutes.status_code == 200
    assert "text/markdown" in minutes.headers["content-type"]
    assert f"{sid}.minutes.md" in minutes.headers["content-disposition"]
    assert "# 会议纪要" in minutes.text


def test_file_converted_session_saves_uploaded_recording_for_review(monkeypatch, tmp_path):
    uploaded_audio = b"RIFFfake-uploaded-wav"
    monkeypatch.setattr(batch_transcribe, "transcribe_file_report", lambda p: _offline_asr_report("先灰度 5%。谁负责回滚？"))
    monkeypatch.setattr(batch_transcribe, "is_available", lambda: True)
    monkeypatch.setattr(llm_service.LlmConfig, "from_env", lambda: None)

    client = TestClient(create_app(data_dir=tmp_path))
    created = client.post(
        "/live/asr/transcribe-file/sessions",
        files={"file": ("meeting.wav", BytesIO(uploaded_audio), "audio/wav")},
    )

    assert created.status_code == 201, created.text
    sid = created.json()["session_id"]
    events = client.get(f"/live/asr/sessions/{sid}/events")
    assert events.status_code == 200
    audio = events.json()["audio"]
    assert audio["saved"] is True
    assert audio["source_type"] == "uploaded_file"
    assert audio["original_filename"] == "meeting.wav"
    assert audio["sha256"] == hashlib.sha256(uploaded_audio).hexdigest()
    audio_path = Path(tmp_path, audio["relative_path"])
    assert audio_path.read_bytes() == uploaded_audio

    download = client.get(f"/live/asr/sessions/{sid}/audio.wav")
    assert download.status_code == 200
    assert download.content == uploaded_audio


def test_transcribe_file_empty_asr_result_is_not_eligible_for_enabled_llm(monkeypatch):
    monkeypatch.setattr(batch_transcribe, "transcribe_file_report", lambda p: _offline_asr_report("   "))
    monkeypatch.setattr(batch_transcribe, "is_available", lambda: True)
    monkeypatch.setattr(llm_service.LlmConfig, "from_env", lambda: None)

    client = TestClient(create_app())
    created = client.post(
        "/live/asr/transcribe-file/sessions",
        files={"file": ("blank.wav", BytesIO(b"fake-audio"), "audio/wav")},
    )

    assert created.status_code == 201, created.text
    sid = created.json()["session_id"]
    events = client.get(f"/live/asr/sessions/{sid}/events")
    assert events.status_code == 200
    body = events.json()
    assert body["final_count"] == 1
    assert body["non_empty_final_count"] == 0
    assert body["non_empty_transcript"] is False
    assert body["event_source"]["input_source"] == "uploaded_file"
    assert body["event_source"]["acceptance_eligible"] is False
    assert "asr_transcript_empty" in body["event_source"]["acceptance_blockers"]

    class ShouldNotCallClient:
        def post_json(self, url, headers, body, timeout):
            raise AssertionError("empty ASR transcript must be blocked before LLM network calls")

    monkeypatch.setattr(llm_service, "HttpxLlmClient", lambda: ShouldNotCallClient())
    monkeypatch.setattr(
        llm_service.LlmConfig,
        "from_env",
        lambda: llm_service.LlmConfig(base_url="https://gw.example", api_key="sk-test", model="m1"),
    )
    response = client.post(f"/live/asr/sessions/{sid}/llm-execution-runs", json={"mode": "enabled"})

    assert response.status_code == 409
    assert "asr_transcript_empty" in response.text


def test_transcribe_file_semantic_quality_warning_does_not_block_general_transcript(monkeypatch):
    monkeypatch.setattr(
        batch_transcribe,
        "transcribe_file_report",
        lambda p: _offline_asr_report("今天天气不错，我们吃饭聊天，然后大家都很开心，下午去散步。"),
    )
    monkeypatch.setattr(batch_transcribe, "is_available", lambda: True)
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gw.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "m1")

    class FakeClient:
        def post_json(self, url, headers, body, timeout):
            return {
                "choices": [{"message": {"content": json.dumps({
                    "suggestion_text": "确认后续安排和负责人。",
                    "confidence": 0.82,
                    "trigger_reason": "会议内容存在待确认事项。",
                }, ensure_ascii=False)}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18},
            }

    monkeypatch.setattr(llm_service, "HttpxLlmClient", lambda: FakeClient())

    client = TestClient(create_app())
    created = client.post(
        "/live/asr/transcribe-file/sessions",
        files={"file": ("daily.wav", BytesIO(b"fake-audio"), "audio/wav")},
    )

    assert created.status_code == 201, created.text
    sid = created.json()["session_id"]
    events = client.get(f"/live/asr/sessions/{sid}/events")
    assert events.status_code == 200
    body = events.json()
    assert body["degradation_reasons"] == []
    assert body["event_source"]["asr_semantic_quality"]["status"] == "warning"
    assert body["event_source"]["asr_semantic_quality"]["quality_warning"] == "technical_context_not_detected"
    assert body["event_source"]["acceptance_eligible"] is True
    assert body["event_source"]["acceptance_blockers"] == []

    response = client.post(f"/live/asr/sessions/{sid}/llm-execution-runs", json={"mode": "enabled"})

    assert response.status_code == 200, response.text
    assert response.json()["run_count"] == 0
    assert response.json()["candidate_selection"]["total_candidates"] == 0


def test_transcribe_file_session_unavailable_when_funasr_missing(monkeypatch):
    monkeypatch.setattr(batch_transcribe, "is_available", lambda: False)
    client = TestClient(create_app())
    r = client.post(
        "/live/asr/transcribe-file/sessions",
        files={"file": ("meeting.wav", BytesIO(b"x"), "audio/wav")},
    )
    assert r.status_code == 422
    assert "unavailable" in r.json()["detail"]


def test_batch_transcribe_file_report_runs_offline_batch_cli(monkeypatch, tmp_path):
    audio = tmp_path / "meeting.wav"
    audio.write_bytes(b"fake")
    fake_python = tmp_path / "python"
    fake_worker = tmp_path / "transcribe_funasr.py"
    fake_model = tmp_path / "offline-model"
    fake_vad = tmp_path / "vad-model"
    fake_punc = tmp_path / "punc-model"
    for path in [fake_python, fake_worker]:
        path.write_text("", encoding="utf-8")
    for path in [fake_model, fake_vad, fake_punc]:
        path.mkdir()

    monkeypatch.setattr(batch_transcribe, "_FUNASR_PY", fake_python)
    monkeypatch.setattr(batch_transcribe, "_TRANSCRIBE_WORKER", fake_worker)
    monkeypatch.setattr(batch_transcribe, "_resolve_offline_model_arg", lambda: str(fake_model))
    monkeypatch.setattr(batch_transcribe, "_resolve_vad_model_arg", lambda: str(fake_vad))
    monkeypatch.setattr(batch_transcribe, "_resolve_punc_model_arg", lambda: str(fake_punc))
    calls = []

    def fake_run(cmd, capture_output, text, timeout):
        calls.append(cmd)
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout=json.dumps({
                "status": "ok",
                "batch_mode": "single_process_reused_funasr_offline_model",
                "transcribe_only_rtf": 0.05,
                "items": [{
                    "status": "ok",
                    "text": "接口先灰度。",
                    "audio_duration_seconds": 3.0,
                    "rtf": 0.05,
                    "raw": {
                        "mode": "file_batch_offline_transcript",
                        "punc_model_status": "enabled",
                    },
                }],
            }, ensure_ascii=False),
            stderr="",
        )

    monkeypatch.setattr(batch_transcribe.subprocess, "run", fake_run)

    report = batch_transcribe.transcribe_file_report(audio)

    assert report["text"] == "接口先灰度。"
    assert report["batch"]["batch_mode"] == "single_process_reused_funasr_offline_model"
    cmd = next(call for call in calls if call and call[0] == str(fake_python))
    assert cmd[:2] == [str(fake_python), str(fake_worker)]
    assert cmd[2] == str(audio.with_name("meeting.16k.wav"))
    assert "--offline-batch" in cmd
    assert cmd[cmd.index("--model") + 1] == str(fake_model)
    assert cmd[cmd.index("--vad-model") + 1] == str(fake_vad)
    assert cmd[cmd.index("--punc-model") + 1] == str(fake_punc)
