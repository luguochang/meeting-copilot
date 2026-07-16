import importlib.util
import json
import math
from pathlib import Path
import wave


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "mainline_evidence_bundle_runner.py"


def load_tool_module():
    spec = importlib.util.spec_from_file_location(
        "mainline_evidence_bundle_runner",
        TOOL_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_file_lane_bundle_writes_traceable_no_go_when_llm_is_mock(tmp_path, monkeypatch):
    tool = load_tool_module()
    from meeting_copilot_web_mvp import asr_correct, batch_transcribe, llm_service

    monkeypatch.setattr(batch_transcribe, "is_available", lambda: True)
    monkeypatch.setattr(
        batch_transcribe,
        "transcribe_file_report",
        lambda _path: {
            "text": "先灰度 5%。谁负责回滚？",
            "raw": {"provider": "funasr", "mode": "file_batch_offline_transcript"},
            "batch": {"batch_mode": "test_fixture"},
        },
    )
    monkeypatch.setattr(asr_correct, "correct_transcript", lambda raw, cfg: (raw, {"total_tokens": 0}, False))

    class FakeClient:
        def post_json(self, url, headers, body, timeout):
            sys_msg = body["messages"][0]["content"] if body.get("messages") else ""
            if "方案考量" in sys_msg:
                content = json.dumps(
                    [
                        {
                            "card_type": "approach.alternative",
                            "suggestion_text": "建议确认是否需要增加 50% 灰度档。",
                            "confidence": 0.82,
                            "trigger_reason": "灰度方案缺少扩容档位",
                            "evidence_quote": "先灰度 5%",
                        }
                    ],
                    ensure_ascii=False,
                )
            elif "会议纪要" in sys_msg:
                content = json.dumps(
                    {
                        "background": "讨论灰度发布。",
                        "decisions": ["先灰度 5%"],
                        "action_items": [],
                        "risks": ["回滚 owner 待确认"],
                        "open_questions": ["谁负责回滚？"],
                        "evidence_quotes": ["先灰度 5%。谁负责回滚？"],
                    },
                    ensure_ascii=False,
                )
            else:
                content = json.dumps(
                    {
                        "suggestion_text": "建议确认回滚 owner。",
                        "confidence": 0.86,
                        "trigger_reason": "候选灰度决策缺少 owner",
                    },
                    ensure_ascii=False,
                )
            return {
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18},
            }

    monkeypatch.setattr(llm_service, "HttpxLlmClient", lambda: FakeClient())
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gw.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "fake-meeting-copilot")
    monkeypatch.setenv("LLM_GATEWAY_IS_MOCK", "true")

    audio_path = tmp_path / "meeting.wav"
    audio_path.write_bytes(b"fake-audio")
    bundle = tool.run_file_lane_bundle(
        audio_path=audio_path,
        artifact_root=tmp_path / "artifacts/tmp/acceptance/run-001",
        data_dir=tmp_path / "runtime-data",
        run_id="run-001",
    )

    manifest = bundle["manifest"]
    assert manifest["schema_version"] == "mainline_evidence_bundle.v1"
    assert manifest["audio_source"] == "uploaded_wav"
    assert manifest["asr_provider"] == "local_funasr_batch"
    assert manifest["asr_fallback_used"] is False
    assert manifest["llm_provider"] == "local_mock_openai"
    assert manifest["llm_called"] is False
    assert manifest["llm_call_count"] == 0
    assert manifest["llm_usage_total_tokens"] == 0
    assert manifest["ui_coverage"] == "API_only"
    assert manifest["verdict"] == "no_go"
    assert "llm_provider_not_real_gateway" in manifest["degradation_reasons"]
    assert "llm_execution_blocked:409" in manifest["degradation_reasons"]
    assert "ui_not_verified_in_browser" in manifest["degradation_reasons"]
    assert manifest["suggestion_card_count"] == 0
    assert manifest["approach_card_count"] == 0
    assert manifest["minutes_char_count"] == 0
    assert manifest["delete_verified"] is True

    artifact_root = Path(bundle["artifact_root"])
    for name in [
        "manifest.json",
        "go_no_go.md",
        "upload_response.json",
        "session_events.json",
        "llm_runs_error.json",
        "delete_response.json",
    ]:
        assert (artifact_root / name).exists(), name

    serialized = json.dumps(json.loads((artifact_root / "manifest.json").read_text()), ensure_ascii=False)
    assert "sk-test" not in serialized
    assert "api_key" not in serialized
    go_no_go = (artifact_root / "go_no_go.md").read_text(encoding="utf-8")
    assert "Verdict: no_go" in go_no_go
    assert "llm_provider_not_real_gateway" in go_no_go


def test_bundle_blocks_missing_audio_without_creating_go(tmp_path):
    tool = load_tool_module()

    bundle = tool.run_file_lane_bundle(
        audio_path=tmp_path / "missing.wav",
        artifact_root=tmp_path / "artifacts/tmp/acceptance/run-missing",
        data_dir=tmp_path / "runtime-data",
        run_id="run-missing",
    )

    assert bundle["manifest"]["verdict"] == "no_go"
    assert "input_audio_missing" in bundle["manifest"]["degradation_reasons"]
    assert (Path(bundle["artifact_root"]) / "manifest.json").exists()
    assert (Path(bundle["artifact_root"]) / "go_no_go.md").exists()


def test_evidence_json_writer_redacts_secret_values(tmp_path, monkeypatch):
    tool = load_tool_module()
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-evidence-secret")
    output = tmp_path / "evidence.json"

    tool._write_json(
        output,
        {
            "api_key": "sk-evidence-secret",
            "authorization": "Bearer sk-evidence-secret",
            "nested": {"text": "token=sk-evidence-secret"},
        },
    )

    serialized = output.read_text(encoding="utf-8")
    assert "sk-evidence-secret" not in serialized
    assert "<redacted>" in serialized


def test_real_mic_degraded_bundle_records_silent_input_as_no_go(tmp_path):
    tool = load_tool_module()
    health_report = {
        "report_mode": "audio_capture_healthcheck",
        "health_status": "blocked_audio_too_quiet",
        "audio_path": "artifacts/tmp/audio_health/silent.wav",
        "duration_seconds": 10.0,
        "sample_rate": 16000,
        "channel_count": 1,
        "rms": 0.0,
        "peak": 0.0,
        "active_sample_ratio": 0.0,
        "silence_ratio": 1.0,
        "capture": {
            "capture_status": "recorded_from_real_microphone",
            "audio_device_index": 0,
            "audio_file_size_bytes": 320044,
        },
    }
    asr_probe = {
        "session_id": "rec_silent_probe",
        "provider": "sherpa_onnx_realtime",
        "provider_mode": "real",
        "is_mock": False,
        "asr_fallback_used": False,
        "degradation_reasons": ["asr_final_empty"],
        "events": [
            {
                "event_type": "evaluation_summary",
                "payload": {
                    "passes_minimum_gate": False,
                    "final_event_count": 0,
                    "end_of_stream_event_count": 1,
                },
            }
        ],
    }

    bundle = tool.run_real_mic_lane_bundle(
        health_report=health_report,
        artifact_root=tmp_path / "artifacts/tmp/acceptance/real-mic-silent",
        run_id="real-mic-silent",
        asr_probe=asr_probe,
        ui_report={
            "ui_coverage": "in_app_browser_manual",
            "workbench_same_session_visible": True,
            "frontend_utterance_count": 0,
            "frontend_card_count": 0,
            "frontend_minutes_visible": False,
            "browser_console_error_count": 0,
            "network_error_count": 0,
        },
    )

    manifest = bundle["manifest"]
    assert manifest["schema_version"] == "mainline_evidence_bundle.v1"
    assert manifest["audio_source"] == "real_mic"
    assert manifest["input_audio_path_kind"] == "user_authorized_runtime_artifact"
    assert manifest["session_id"] == "rec_silent_probe"
    assert manifest["asr_provider"] == "sherpa_onnx_realtime"
    assert manifest["asr_provider_mode"] == "real"
    assert manifest["asr_fallback_used"] is False
    assert manifest["transcript_char_count"] == 0
    assert manifest["final_segment_count"] == 0
    assert manifest["real_mic_health_status"] == "blocked_audio_too_quiet"
    assert manifest["real_mic_peak"] == 0.0
    assert manifest["real_mic_rms"] == 0.0
    assert manifest["verdict"] == "no_go"
    assert "real_mic_audio_too_quiet" in manifest["degradation_reasons"]
    assert "asr_final_empty" in manifest["degradation_reasons"]
    assert "final_segment_missing" in manifest["degradation_reasons"]
    assert "ui_not_headless_verified" in manifest["degradation_reasons"]

    artifact_root = Path(bundle["artifact_root"])
    assert (artifact_root / "manifest.json").exists()
    assert (artifact_root / "go_no_go.md").exists()
    assert (artifact_root / "real_mic_health_report.json").exists()
    assert (artifact_root / "asr_probe.json").exists()
    go_no_go = (artifact_root / "go_no_go.md").read_text(encoding="utf-8")
    assert "Verdict: no_go" in go_no_go
    assert "real_mic_audio_too_quiet" in go_no_go
    assert "- real_mic_health_report.json" in go_no_go
    assert "- asr_probe.json" in go_no_go
    assert "- upload_response.json" not in go_no_go
    assert "- llm_runs.json" not in go_no_go
    assert "- minutes.md" not in go_no_go


def test_file_lane_bundle_can_go_when_real_llm_and_ui_same_session_are_verified(tmp_path, monkeypatch):
    tool = load_tool_module()
    from meeting_copilot_web_mvp import asr_correct, batch_transcribe, llm_service

    monkeypatch.setattr(batch_transcribe, "is_available", lambda: True)
    monkeypatch.setattr(
        batch_transcribe,
        "transcribe_file_report",
        lambda _path: {
            "text": "先灰度 5%。谁负责回滚？",
            "raw": {"provider": "funasr", "mode": "file_batch_offline_transcript"},
            "batch": {"batch_mode": "test_fixture"},
        },
    )
    monkeypatch.setattr(asr_correct, "correct_transcript", lambda raw, cfg: (raw, {"total_tokens": 0}, False))

    class FakeClient:
        def post_json(self, url, headers, body, timeout):
            sys_msg = body["messages"][0]["content"] if body.get("messages") else ""
            if "方案考量" in sys_msg:
                content = json.dumps(
                    [
                        {
                            "card_type": "approach.alternative",
                            "suggestion_text": "建议确认扩容档位。",
                            "confidence": 0.82,
                            "trigger_reason": "灰度方案缺少扩容档位",
                            "evidence_quote": "先灰度 5%",
                        }
                    ],
                    ensure_ascii=False,
                )
            elif "会议纪要" in sys_msg:
                content = json.dumps(
                    {
                        "background": "讨论灰度发布。",
                        "decisions": ["先灰度 5%"],
                        "action_items": [],
                        "risks": [],
                        "open_questions": ["谁负责回滚？"],
                        "evidence_quotes": ["谁负责回滚？"],
                    },
                    ensure_ascii=False,
                )
            else:
                content = json.dumps(
                    {
                        "suggestion_text": "建议确认回滚 owner。",
                        "confidence": 0.86,
                        "trigger_reason": "候选灰度决策缺少 owner",
                    },
                    ensure_ascii=False,
                )
            return {
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18},
            }

    def fake_ui_verifier(*, session_id, data_dir, artifact_root):
        screenshot = artifact_root / "workbench-after.png"
        screenshot.write_bytes(b"png")
        return {
            "ui_coverage": "headless_chrome",
            "workbench_same_session_visible": True,
            "frontend_utterance_count": 1,
            "frontend_card_count": 2,
            "frontend_minutes_visible": True,
            "browser_console_error_count": 0,
            "network_error_count": 0,
            "screenshot_path": "workbench-after.png",
        }

    monkeypatch.setattr(llm_service, "HttpxLlmClient", lambda: FakeClient())
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gw.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "gpt-5.5")
    monkeypatch.delenv("LLM_GATEWAY_IS_MOCK", raising=False)

    audio_path = tmp_path / "meeting.wav"
    audio_path.write_bytes(b"fake-audio")
    bundle = tool.run_file_lane_bundle(
        audio_path=audio_path,
        artifact_root=tmp_path / "artifacts/tmp/acceptance/run-go",
        data_dir=tmp_path / "runtime-data",
        run_id="run-go",
        ui_verifier=fake_ui_verifier,
    )

    manifest = bundle["manifest"]
    assert manifest["verdict"] == "go"
    assert manifest["ui_coverage"] == "headless_chrome"
    assert manifest["workbench_same_session_visible"] is True
    assert manifest["frontend_utterance_count"] == 1
    assert manifest["frontend_card_count"] == 2
    assert manifest["frontend_minutes_visible"] is True
    assert manifest["degradation_reasons"] == []


def test_simulated_realtime_lane_bundle_streams_wav_to_ws_and_writes_traceable_bundle(tmp_path, monkeypatch):
    tool = load_tool_module()
    from meeting_copilot_web_mvp import asr_stream, llm_service

    class EngineeringContractRecognizer:
        provider = "test_contract_realtime_asr"
        provider_mode = "real"
        is_mock = False
        fallback_used = False
        degradation_reasons = []

        def __init__(self, session_id):
            self.session_id = session_id
            self._seq = 0

        def recognize_chunk(self, pcm):
            self._seq += 1
            return [{
                "event_type": "partial",
                "segment_id": "sim_seg",
                "text": "先灰度",
                "start_ms": 0,
                "end_ms": self._seq * 300,
                "confidence": 0.81,
            }]

        def finalize(self):
            return [{
                "event_type": "final",
                "segment_id": "sim_seg",
                "text": "先灰度 5%。谁负责回滚？如果错误率超过 0.1% 就回滚。",
                "start_ms": 0,
                "end_ms": self._seq * 300,
                "confidence": 0.91,
            }]

    class FakeClient:
        def post_json(self, url, headers, body, timeout):
            sys_msg = body["messages"][0]["content"] if body.get("messages") else ""
            if "方案考量" in sys_msg:
                content = json.dumps(
                    [
                        {
                            "card_type": "approach.alternative",
                            "suggestion_text": "建议补充 20% 灰度档位和回滚演练。",
                            "confidence": 0.83,
                            "trigger_reason": "灰度方案只有 5% 档位",
                            "evidence_quote": "先灰度 5%",
                        }
                    ],
                    ensure_ascii=False,
                )
            elif "会议纪要" in sys_msg:
                content = json.dumps(
                    {
                        "background": "讨论灰度发布。",
                        "decisions": ["先灰度 5%"],
                        "action_items": [],
                        "risks": ["错误率超过 0.1% 需要回滚"],
                        "open_questions": ["谁负责回滚？"],
                        "evidence_quotes": ["谁负责回滚？"],
                    },
                    ensure_ascii=False,
                )
            else:
                content = json.dumps(
                    {
                        "suggestion_text": "建议确认回滚 owner。",
                        "confidence": 0.87,
                        "trigger_reason": "灰度决策缺少回滚负责人",
                    },
                    ensure_ascii=False,
                )
            return {
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18},
            }

    def fake_ui_verifier(*, session_id, data_dir, artifact_root):
        return {
            "ui_coverage": "headless_chrome",
            "workbench_same_session_visible": True,
            "frontend_utterance_count": 1,
            "frontend_card_count": 2,
            "frontend_minutes_visible": True,
            "browser_console_error_count": 0,
            "network_error_count": 0,
        }

    monkeypatch.setattr(asr_stream, "get_recognizer", lambda sid: EngineeringContractRecognizer(sid))
    monkeypatch.setattr(asr_stream, "_correct_transcript", lambda raw, cfg: (raw, {"total_tokens": 0}, False))
    monkeypatch.setattr(llm_service, "HttpxLlmClient", lambda: FakeClient())
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gw.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "gpt-5.5")
    monkeypatch.delenv("LLM_GATEWAY_IS_MOCK", raising=False)

    audio_path = tmp_path / "simulated-meeting.wav"
    _write_pcm16_wav(audio_path)

    bundle = tool.run_simulated_realtime_lane_bundle(
        audio_path=audio_path,
        artifact_root=tmp_path / "artifacts/tmp/acceptance/simulated-realtime",
        data_dir=tmp_path / "runtime-data",
        run_id="simulated-realtime",
        ui_verifier=fake_ui_verifier,
    )

    manifest = bundle["manifest"]
    assert manifest["audio_source"] == "simulated_realtime_wav"
    assert manifest["input_audio_path_kind"] == "user_authorized_runtime_artifact"
    assert manifest["counts_as_real_mic_go_evidence"] is False
    assert manifest["asr_provider"] == "test_contract_realtime_asr"
    assert manifest["asr_provider_mode"] == "real"
    assert manifest["asr_fallback_used"] is False
    assert manifest["asr_semantic_quality_status"] == "passed"
    assert manifest["asr_semantic_quality_blocked"] is False
    assert manifest["llm_provider"] == "real_gateway"
    assert manifest["llm_called"] is True
    assert manifest["llm_call_count"] == 5
    assert manifest["llm_usage_total_tokens"] == 90
    assert manifest["final_segment_count"] >= 1
    assert manifest["suggestion_card_count"] >= 1
    assert manifest["approach_card_count"] >= 1
    assert manifest["minutes_char_count"] > 0
    assert manifest["ui_coverage"] == "headless_chrome"
    assert manifest["delete_verified"] is True
    assert manifest["verdict"] == "go"
    assert manifest["degradation_reasons"] == []

    artifact_root = Path(bundle["artifact_root"])
    for name in [
        "manifest.json",
        "go_no_go.md",
        "ws_events.json",
        "session_events.json",
        "llm_runs.json",
        "suggestion_cards.json",
        "approach_cards.json",
        "minutes.md",
        "minutes.json",
        "delete_response.json",
    ]:
        assert (artifact_root / name).exists(), name

    session_events = json.loads((artifact_root / "session_events.json").read_text(encoding="utf-8"))
    assert session_events["event_source"]["input_source"] == "simulated_realtime_wav"
    assert session_events["event_source"]["acceptance_eligible"] is True
    assert session_events["event_source"]["asr_semantic_quality"]["status"] == "passed"


def test_workbench_session_verifier_passes_absolute_runtime_paths(tmp_path, monkeypatch):
    tool = load_tool_module()
    captured: dict[str, str] = {}
    artifact_root = tmp_path / "artifacts" / "acceptance" / "ui"
    relative_data_dir = Path("artifacts/tmp/release_acceptance/relative-runtime")

    class FakeProc:
        returncode = 0
        stdout = "{}"
        stderr = ""

    def fake_run(command, cwd, env, text, capture_output, timeout):
        captured["data_dir"] = env["MEETING_COPILOT_DATA_DIR"]
        captured["artifact_root"] = env["MEETING_COPILOT_ARTIFACT_ROOT"]
        artifact_root.mkdir(parents=True, exist_ok=True)
        (artifact_root / "ui_verification.json").write_text(
            json.dumps(
                {
                    "ui_coverage": "headless_chrome",
                    "workbench_same_session_visible": True,
                    "frontend_utterance_count": 1,
                    "frontend_card_count": 2,
                    "frontend_minutes_visible": True,
                    "browser_console_error_count": 0,
                    "network_error_count": 0,
                }
            ),
            encoding="utf-8",
        )
        return FakeProc()

    monkeypatch.setattr(tool.subprocess, "run", fake_run)

    report = tool.verify_workbench_same_session(
        session_id="release_ui_path_probe",
        data_dir=relative_data_dir,
        artifact_root=artifact_root,
    )

    assert report["workbench_same_session_visible"] is True
    assert Path(captured["data_dir"]).is_absolute()
    assert captured["data_dir"] == str((tool.REPO_ROOT / relative_data_dir).resolve())
    assert Path(captured["artifact_root"]).is_absolute()
    assert captured["artifact_root"] == str(artifact_root.resolve())


def test_simulated_realtime_lane_bundle_blocks_bad_semantic_quality(tmp_path, monkeypatch):
    tool = load_tool_module()
    from meeting_copilot_web_mvp import asr_stream, llm_service

    class BadSemanticRecognizer:
        provider = "test_contract_realtime_asr"
        provider_mode = "real"
        is_mock = False
        fallback_used = False
        degradation_reasons = []

        def __init__(self, session_id):
            self.session_id = session_id
            self._seq = 0

        def recognize_chunk(self, pcm):
            self._seq += 1
            return [{
                "event_type": "partial",
                "segment_id": "bad_semantic",
                "text": "啊嗯这个那个然后就是可以吧request xt moden",
                "start_ms": 0,
                "end_ms": self._seq * 300,
                "confidence": 0.9,
            }]

        def finalize(self):
            return [{
                "event_type": "final",
                "segment_id": "bad_semantic",
                "text": "啊嗯这个那个然后就是可以吧一二三四五六七八 request xt moden downtwo calling methoc ine ofdel",
                "start_ms": 0,
                "end_ms": self._seq * 300,
                "confidence": 0.9,
            }]

    class ShouldNotCallClient:
        def post_json(self, url, headers, body, timeout):
            raise AssertionError("semantic-quality-blocked bundle must not call LLM")

    monkeypatch.setattr(asr_stream, "get_recognizer", lambda sid: BadSemanticRecognizer(sid))
    monkeypatch.setattr(asr_stream, "_correct_transcript", lambda raw, cfg: (raw, {"total_tokens": 0}, False))
    monkeypatch.setattr(llm_service, "HttpxLlmClient", lambda: ShouldNotCallClient())
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gw.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "gpt-5.5")
    monkeypatch.delenv("LLM_GATEWAY_IS_MOCK", raising=False)

    audio_path = tmp_path / "bad-semantic.wav"
    _write_pcm16_wav(audio_path)

    bundle = tool.run_simulated_realtime_lane_bundle(
        audio_path=audio_path,
        artifact_root=tmp_path / "artifacts/tmp/acceptance/bad-semantic",
        data_dir=tmp_path / "runtime-data",
        run_id="bad-semantic",
    )

    manifest = bundle["manifest"]
    assert manifest["asr_semantic_quality_status"] == "blocked"
    assert manifest["asr_semantic_quality_blocked"] is True
    assert manifest["verdict"] == "no_go"
    assert "asr_semantic_quality_blocked" in manifest["degradation_reasons"]
    assert "llm_execution_blocked:409" in manifest["degradation_reasons"]


def test_real_mic_recorded_realtime_lane_requires_passed_health_before_streaming(tmp_path):
    tool = load_tool_module()
    health_report = {
        "report_mode": "audio_capture_healthcheck",
        "health_status": "blocked_audio_too_quiet",
        "audio_path": "artifacts/tmp/audio_health/quiet.wav",
        "duration_seconds": 15.0,
        "sample_rate": 16000,
        "channel_count": 1,
        "rms": 0.0,
        "peak": 0.0,
        "active_sample_ratio": 0.0,
        "silence_ratio": 1.0,
        "capture": {"capture_status": "recorded_from_real_microphone"},
        "privacy_cost_flags": {
            "raw_audio_uploaded": False,
            "remote_asr_called": False,
            "llm_called": False,
            "configs_local_read": False,
            "user_audio_committed_to_repo": False,
        },
    }
    audio_path = tmp_path / "quiet-real-mic.wav"
    _write_pcm16_wav(audio_path)

    bundle = tool.run_real_mic_recorded_realtime_lane_bundle(
        audio_path=audio_path,
        health_report=health_report,
        artifact_root=tmp_path / "artifacts/tmp/acceptance/real-mic-recorded-blocked",
        data_dir=tmp_path / "runtime-data",
        run_id="real-mic-recorded-blocked",
    )

    manifest = bundle["manifest"]
    assert manifest["audio_source"] == "real_mic_recorded_wav"
    assert manifest["real_mic_health_status"] == "blocked_audio_too_quiet"
    assert manifest["counts_as_real_mic_go_evidence"] is False
    assert manifest["browser_live_mic_go_evidence"] is False
    assert manifest["llm_called"] is False
    assert manifest["asr_provider"] == "not_started"
    assert manifest["verdict"] == "no_go"
    assert "real_mic_audio_too_quiet" in manifest["degradation_reasons"]

    artifact_root = Path(bundle["artifact_root"])
    assert (artifact_root / "real_mic_health_report.json").exists()
    assert not (artifact_root / "ws_events.json").exists()
    assert not (artifact_root / "llm_runs.json").exists()


def test_real_mic_recorded_realtime_lane_streams_real_mic_wav_to_ws_and_writes_traceable_bundle(tmp_path, monkeypatch):
    tool = load_tool_module()
    from meeting_copilot_web_mvp import asr_stream, llm_service

    class EngineeringContractRecognizer:
        provider = "test_contract_realtime_asr"
        provider_mode = "real"
        is_mock = False
        fallback_used = False
        degradation_reasons = []

        def __init__(self, session_id):
            self.session_id = session_id
            self._seq = 0

        def recognize_chunk(self, pcm):
            self._seq += 1
            return [{
                "event_type": "partial",
                "segment_id": "real_mic_seg",
                "text": "先灰度",
                "start_ms": 0,
                "end_ms": self._seq * 300,
                "confidence": 0.81,
            }]

        def finalize(self):
            return [{
                "event_type": "final",
                "segment_id": "real_mic_seg",
                "text": "先灰度 5%。谁负责回滚？如果错误率超过 0.1% 就回滚。",
                "start_ms": 0,
                "end_ms": self._seq * 300,
                "confidence": 0.91,
            }]

    class FakeClient:
        def post_json(self, url, headers, body, timeout):
            sys_msg = body["messages"][0]["content"] if body.get("messages") else ""
            if "方案考量" in sys_msg:
                content = json.dumps(
                    [
                        {
                            "card_type": "approach.alternative",
                            "suggestion_text": "建议补充 20% 灰度档位和回滚演练。",
                            "confidence": 0.83,
                            "trigger_reason": "灰度方案只有 5% 档位",
                            "evidence_quote": "先灰度 5%",
                        }
                    ],
                    ensure_ascii=False,
                )
            elif "会议纪要" in sys_msg:
                content = json.dumps(
                    {
                        "background": "讨论灰度发布。",
                        "decisions": ["先灰度 5%"],
                        "action_items": [],
                        "risks": ["错误率超过 0.1% 需要回滚"],
                        "open_questions": ["谁负责回滚？"],
                        "evidence_quotes": ["谁负责回滚？"],
                    },
                    ensure_ascii=False,
                )
            else:
                content = json.dumps(
                    {
                        "suggestion_text": "建议确认回滚 owner。",
                        "confidence": 0.87,
                        "trigger_reason": "灰度决策缺少回滚负责人",
                    },
                    ensure_ascii=False,
                )
            return {
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18},
            }

    def fake_ui_verifier(*, session_id, data_dir, artifact_root):
        return {
            "ui_coverage": "headless_chrome",
            "workbench_same_session_visible": True,
            "frontend_utterance_count": 1,
            "frontend_card_count": 2,
            "frontend_minutes_visible": True,
            "browser_console_error_count": 0,
            "network_error_count": 0,
        }

    monkeypatch.setattr(asr_stream, "get_recognizer", lambda sid: EngineeringContractRecognizer(sid))
    monkeypatch.setattr(asr_stream, "_correct_transcript", lambda raw, cfg: (raw, {"total_tokens": 0}, False))
    monkeypatch.setattr(llm_service, "HttpxLlmClient", lambda: FakeClient())
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gw.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "gpt-5.5")
    monkeypatch.delenv("LLM_GATEWAY_IS_MOCK", raising=False)

    audio_path = tmp_path / "real-mic-recorded.wav"
    _write_pcm16_wav(audio_path)
    health_report = {
        "report_mode": "audio_capture_healthcheck",
        "health_status": "audio_capture_health_passed",
        "audio_path": str(audio_path),
        "duration_seconds": 15.0,
        "sample_rate": 16000,
        "channel_count": 1,
        "rms": 0.04,
        "peak": 0.3,
        "active_sample_ratio": 0.8,
        "silence_ratio": 0.2,
        "capture": {"capture_status": "recorded_from_real_microphone"},
        "privacy_cost_flags": {
            "raw_audio_uploaded": False,
            "remote_asr_called": False,
            "llm_called": False,
            "configs_local_read": False,
            "user_audio_committed_to_repo": False,
        },
    }

    bundle = tool.run_real_mic_recorded_realtime_lane_bundle(
        audio_path=audio_path,
        health_report=health_report,
        artifact_root=tmp_path / "artifacts/tmp/acceptance/real-mic-recorded",
        data_dir=tmp_path / "runtime-data",
        run_id="real-mic-recorded",
        ui_verifier=fake_ui_verifier,
    )

    manifest = bundle["manifest"]
    assert manifest["audio_source"] == "real_mic_recorded_wav"
    assert manifest["input_audio_path_kind"] == "user_authorized_runtime_artifact"
    assert manifest["counts_as_real_mic_go_evidence"] is True
    assert manifest["browser_live_mic_go_evidence"] is False
    assert manifest["real_mic_health_status"] == "audio_capture_health_passed"
    assert manifest["asr_provider"] == "test_contract_realtime_asr"
    assert manifest["asr_provider_mode"] == "real"
    assert manifest["asr_fallback_used"] is False
    assert manifest["llm_provider"] == "real_gateway"
    assert manifest["llm_called"] is True
    assert manifest["final_segment_count"] >= 1
    assert manifest["suggestion_card_count"] >= 1
    assert manifest["approach_card_count"] >= 1
    assert manifest["minutes_char_count"] > 0
    assert manifest["ui_coverage"] == "headless_chrome"
    assert manifest["delete_verified"] is True
    assert manifest["verdict"] == "go"
    assert manifest["degradation_reasons"] == []

    artifact_root = Path(bundle["artifact_root"])
    for name in [
        "manifest.json",
        "go_no_go.md",
        "real_mic_health_report.json",
        "input_audio.sha256",
        "ws_events.json",
        "session_events.json",
        "llm_runs.json",
        "suggestion_cards.json",
        "approach_cards.json",
        "minutes.md",
        "minutes.json",
        "delete_response.json",
        "sessions_list_before_delete.json",
        "sessions_list_after_delete.json",
    ]:
        assert (artifact_root / name).exists(), name

    session_events = json.loads((artifact_root / "session_events.json").read_text(encoding="utf-8"))
    assert session_events["event_source"]["input_source"] == "real_mic_recorded_wav"
    assert session_events["event_source"]["acceptance_eligible"] is True


def test_browser_live_mic_lane_no_go_when_health_passes_but_asr_final_missing(tmp_path):
    tool = load_tool_module()
    browser_health = {
        "report_type": "workbench_browser_mic_health",
        "session_id": "rec_browser_missing_final",
        "health_status": "audio_capture_health_passed",
        "sample_count": 160000,
        "chunk_count": 30,
        "rms": 0.04,
        "peak": 0.3,
        "active_sample_ratio": 0.8,
        "raw_audio_uploaded": False,
        "remote_asr_called": False,
        "llm_called": False,
    }
    asr_probe = {
        "session_id": "rec_browser_missing_final",
        "provider": "sherpa_onnx_realtime",
        "provider_mode": "real",
        "is_mock": False,
        "asr_fallback_used": False,
        "degradation_reasons": [],
        "events": [{"event_type": "end_of_stream", "payload": {}}],
    }

    bundle = tool.run_browser_live_mic_lane_bundle(
        browser_mic_health=browser_health,
        artifact_root=tmp_path / "artifacts/tmp/acceptance/browser-live-mic-missing-final",
        run_id="browser-live-mic-missing-final",
        asr_probe=asr_probe,
        ui_report={
            "ui_coverage": "headless_chrome",
            "workbench_same_session_visible": True,
            "frontend_utterance_count": 0,
            "frontend_card_count": 0,
            "frontend_minutes_visible": False,
            "browser_console_error_count": 0,
            "network_error_count": 0,
        },
    )

    manifest = bundle["manifest"]
    assert manifest["audio_source"] == "browser_live_mic"
    assert manifest["browser_live_mic_go_evidence"] is False
    assert manifest["counts_as_real_mic_go_evidence"] is False
    assert manifest["browser_mic_health_status"] == "audio_capture_health_passed"
    assert manifest["browser_mic_chunk_count"] == 30
    assert manifest["asr_provider"] == "sherpa_onnx_realtime"
    assert manifest["asr_provider_mode"] == "real"
    assert manifest["asr_fallback_used"] is False
    assert manifest["final_segment_count"] == 0
    assert manifest["transcript_char_count"] == 0
    assert manifest["verdict"] == "no_go"
    assert "final_segment_missing" in manifest["degradation_reasons"]
    assert "transcript_empty" in manifest["degradation_reasons"]
    assert "browser_live_mic_not_proven" in manifest["degradation_reasons"]

    artifact_root = Path(bundle["artifact_root"])
    assert (artifact_root / "browser_mic_health_report.json").exists()
    assert (artifact_root / "asr_probe.json").exists()
    go_no_go = (artifact_root / "go_no_go.md").read_text(encoding="utf-8")
    assert "Verdict: no_go" in go_no_go
    assert "browser_live_mic_not_proven" in go_no_go


def test_browser_live_mic_lane_can_go_with_complete_same_session_evidence(tmp_path):
    tool = load_tool_module()
    browser_health = {
        "report_type": "workbench_browser_mic_health",
        "session_id": "rec_browser_complete",
        "health_status": "audio_capture_health_passed",
        "sample_count": 160000,
        "chunk_count": 32,
        "rms": 0.045,
        "peak": 0.35,
        "active_sample_ratio": 0.82,
        "raw_audio_uploaded": False,
        "remote_asr_called": False,
        "llm_called": False,
    }
    asr_probe = {
        "session_id": "rec_browser_complete",
        "provider": "sherpa_onnx_realtime",
        "provider_mode": "real",
        "is_mock": False,
        "asr_fallback_used": False,
        "degradation_reasons": [],
        "events": [
            {
                "event_type": "transcript_final",
                "payload": {
                    "normalized_text": "先灰度 5%。谁负责回滚？如果错误率超过 0.1% 就回滚。",
                    "segment_id": "browser_seg_001",
                },
            }
        ],
        "llm_called": True,
        "llm_call_count": 3,
        "llm_usage_total_tokens": 1200,
        "suggestion_card_count": 2,
        "approach_card_count": 1,
        "minutes_char_count": 120,
        "all_cards_have_evidence": True,
        "delete_verified": True,
    }

    bundle = tool.run_browser_live_mic_lane_bundle(
        browser_mic_health=browser_health,
        artifact_root=tmp_path / "artifacts/tmp/acceptance/browser-live-mic-complete",
        run_id="browser-live-mic-complete",
        asr_probe=asr_probe,
        ui_report={
            "ui_coverage": "headless_chrome",
            "workbench_same_session_visible": True,
            "frontend_utterance_count": 1,
            "frontend_card_count": 3,
            "frontend_minutes_visible": True,
            "browser_console_error_count": 0,
            "network_error_count": 0,
        },
    )

    manifest = bundle["manifest"]
    assert manifest["audio_source"] == "browser_live_mic"
    assert manifest["browser_live_mic_go_evidence"] is True
    assert manifest["counts_as_real_mic_go_evidence"] is True
    assert manifest["browser_mic_health_status"] == "audio_capture_health_passed"
    assert manifest["asr_provider_mode"] == "real"
    assert manifest["asr_fallback_used"] is False
    assert manifest["llm_called"] is True
    assert manifest["transcript_char_count"] >= 30
    assert manifest["final_segment_count"] >= 1
    assert manifest["suggestion_card_count"] == 2
    assert manifest["approach_card_count"] == 1
    assert manifest["minutes_char_count"] == 120
    assert manifest["workbench_same_session_visible"] is True
    assert manifest["delete_verified"] is True
    assert manifest["verdict"] == "go"
    assert manifest["degradation_reasons"] == []

    artifact_root = Path(bundle["artifact_root"])
    assert (artifact_root / "browser_mic_health_report.json").exists()
    assert (artifact_root / "asr_probe.json").exists()
    assert (artifact_root / "ui_verification.json").exists()


def test_browser_live_mic_lane_accepts_visible_chrome_as_real_user_mic_ui_evidence(tmp_path):
    tool = load_tool_module()
    browser_health = {
        "report_type": "workbench_browser_mic_health",
        "session_id": "rec_browser_visible_complete",
        "health_status": "audio_capture_health_passed",
        "sample_count": 160000,
        "chunk_count": 32,
        "rms": 0.045,
        "peak": 0.35,
        "active_sample_ratio": 0.82,
        "raw_audio_uploaded": False,
        "remote_asr_called": False,
        "llm_called": False,
    }
    asr_probe = {
        "session_id": "rec_browser_visible_complete",
        "provider": "sherpa_onnx_realtime",
        "provider_mode": "real",
        "is_mock": False,
        "asr_fallback_used": False,
        "degradation_reasons": [],
        "asr_semantic_quality": {"schema_version": "asr_semantic_quality.v1", "status": "passed"},
        "events": [
            {
                "event_type": "transcript_final",
                "payload": {
                    "normalized_text": "我们正在做发布评审，先灰度 5%。如果 P99 超过九百毫秒就回滚，接口负责人确认监控和测试。",
                    "segment_id": "browser_seg_001",
                },
            }
        ],
        "llm_called": True,
        "llm_provider": "real_gateway",
        "llm_call_count": 5,
        "llm_usage_total_tokens": 24300,
        "suggestion_card_count": 2,
        "approach_card_count": 1,
        "minutes_char_count": 120,
        "all_cards_have_evidence": True,
        "delete_verified": True,
    }

    bundle = tool.run_browser_live_mic_lane_bundle(
        browser_mic_health=browser_health,
        artifact_root=tmp_path / "artifacts/tmp/acceptance/browser-live-mic-visible-complete",
        run_id="browser-live-mic-visible-complete",
        asr_probe=asr_probe,
        ui_report={
            "ui_coverage": "visible_chrome",
            "workbench_same_session_visible": True,
            "frontend_utterance_count": 1,
            "frontend_card_count": 3,
            "frontend_minutes_visible": True,
            "browser_console_error_count": 0,
            "network_error_count": 0,
        },
    )

    manifest = bundle["manifest"]
    assert manifest["ui_coverage"] == "visible_chrome"
    assert manifest["asr_semantic_quality_status"] == "passed"
    assert manifest["llm_call_count"] == 5
    assert manifest["llm_usage_total_tokens"] == 24300
    assert manifest["privacy_cost_flags"]["llm_called"] is True
    assert manifest["browser_live_mic_go_evidence"] is True
    assert manifest["counts_as_real_mic_go_evidence"] is True
    assert manifest["verdict"] == "go"
    assert "ui_not_verified_in_browser" not in manifest["degradation_reasons"]
    assert "ui_not_headless_verified" not in manifest["degradation_reasons"]


def test_browser_live_mic_lane_blocks_production_llm_claim_without_usage_evidence(tmp_path):
    tool = load_tool_module()
    browser_health = {
        "report_type": "workbench_browser_mic_health",
        "session_id": "rec_browser_prod_missing_usage",
        "health_status": "audio_capture_health_passed",
        "sample_count": 160000,
        "chunk_count": 32,
        "rms": 0.045,
        "peak": 0.35,
        "active_sample_ratio": 0.82,
        "raw_audio_uploaded": False,
        "remote_asr_called": False,
        "llm_called": False,
    }
    asr_probe = {
        "session_id": "rec_browser_prod_missing_usage",
        "provider": "sherpa_onnx_realtime",
        "provider_mode": "real",
        "is_mock": False,
        "asr_fallback_used": False,
        "degradation_reasons": [],
        "asr_semantic_quality": {"schema_version": "asr_semantic_quality.v1", "status": "passed"},
        "events": [
            {
                "event_type": "transcript_final",
                "payload": {
                    "normalized_text": "我们正在做发布评审，先灰度 5%。如果 P99 超过九百毫秒就回滚，接口负责人确认监控和测试。",
                    "segment_id": "browser_seg_001",
                },
            }
        ],
        "derivation_mode": "production_enabled",
        "llm_called": True,
        "llm_provider": "real_gateway",
        "gateway_base_url_kind": "remote",
        "counts_as_production_llm_evidence": False,
        "llm_call_count": 0,
        "llm_usage_total_tokens": 0,
        "suggestion_card_count": 2,
        "approach_card_count": 1,
        "minutes_char_count": 120,
        "all_cards_have_evidence": True,
        "delete_verified": True,
    }

    bundle = tool.run_browser_live_mic_lane_bundle(
        browser_mic_health=browser_health,
        artifact_root=tmp_path / "artifacts/tmp/acceptance/browser-live-mic-prod-missing-usage",
        run_id="browser-live-mic-prod-missing-usage",
        asr_probe=asr_probe,
        ui_report={
            "ui_coverage": "visible_chrome",
            "workbench_same_session_visible": True,
            "frontend_utterance_count": 1,
            "frontend_card_count": 3,
            "frontend_minutes_visible": True,
            "browser_console_error_count": 0,
            "network_error_count": 0,
        },
    )

    manifest = bundle["manifest"]
    assert manifest["verdict"] == "no_go"
    assert manifest["browser_live_mic_go_evidence"] is False
    assert manifest["counts_as_production_llm_evidence"] is False
    assert "browser_live_mic_production_llm_evidence_missing" in manifest["degradation_reasons"]
    assert "browser_live_mic_llm_usage_evidence_missing" in manifest["degradation_reasons"]


def test_browser_live_mic_lane_blocks_short_transcript_even_with_complete_cards(tmp_path):
    tool = load_tool_module()
    browser_health = {
        "report_type": "workbench_browser_mic_health",
        "session_id": "rec_browser_short_text",
        "health_status": "audio_capture_health_passed",
        "sample_count": 160000,
        "chunk_count": 32,
        "rms": 0.045,
        "peak": 0.35,
        "active_sample_ratio": 0.82,
        "raw_audio_uploaded": False,
        "remote_asr_called": False,
        "llm_called": False,
    }
    asr_probe = {
        "session_id": "rec_browser_short_text",
        "provider": "sherpa_onnx_realtime",
        "provider_mode": "real",
        "is_mock": False,
        "asr_fallback_used": False,
        "degradation_reasons": [],
        "events": [
            {
                "event_type": "transcript_final",
                "payload": {
                    "normalized_text": "挺好的",
                    "segment_id": "browser_seg_short",
                },
            }
        ],
        "llm_called": True,
        "suggestion_card_count": 2,
        "approach_card_count": 1,
        "minutes_char_count": 120,
        "all_cards_have_evidence": True,
        "delete_verified": True,
    }

    bundle = tool.run_browser_live_mic_lane_bundle(
        browser_mic_health=browser_health,
        artifact_root=tmp_path / "artifacts/tmp/acceptance/browser-live-mic-short-text",
        run_id="browser-live-mic-short-text",
        asr_probe=asr_probe,
        ui_report={
            "ui_coverage": "headless_chrome",
            "workbench_same_session_visible": True,
            "frontend_utterance_count": 1,
            "frontend_card_count": 3,
            "frontend_minutes_visible": True,
            "browser_console_error_count": 0,
            "network_error_count": 0,
        },
    )

    manifest = bundle["manifest"]
    assert manifest["audio_source"] == "browser_live_mic"
    assert manifest["transcript_char_count"] < 30
    assert manifest["verdict"] == "no_go"
    assert manifest["browser_live_mic_go_evidence"] is False
    assert manifest["counts_as_real_mic_go_evidence"] is False
    assert "browser_live_mic_transcript_too_short" in manifest["degradation_reasons"]
    assert "browser_live_mic_not_proven" in manifest["degradation_reasons"]


def test_browser_live_mic_cli_requires_browser_health_report(tmp_path, capsys):
    tool = load_tool_module()

    try:
        tool.main([
            "--lane", "browser-live-mic",
            "--artifact-root", str(tmp_path / "artifacts/tmp/acceptance/browser-cli-missing"),
            "--run-id", "browser-cli-missing",
        ])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("expected parser to reject missing --browser-mic-health-report")

    captured = capsys.readouterr()
    assert "--browser-mic-health-report is required" in captured.err


def test_browser_live_mic_cli_writes_no_go_bundle_from_reports(tmp_path):
    tool = load_tool_module()
    health_path = tmp_path / "browser-health.json"
    asr_path = tmp_path / "asr-probe.json"
    ui_path = tmp_path / "ui-report.json"
    health_path.write_text(json.dumps({
        "report_type": "workbench_browser_mic_health",
        "session_id": "rec_browser_cli",
        "health_status": "audio_capture_health_passed",
        "sample_count": 160000,
        "chunk_count": 31,
        "rms": 0.04,
        "peak": 0.3,
        "active_sample_ratio": 0.8,
        "raw_audio_uploaded": False,
        "remote_asr_called": False,
        "llm_called": False,
    }), encoding="utf-8")
    asr_path.write_text(json.dumps({
        "session_id": "rec_browser_cli",
        "provider": "sherpa_onnx_realtime",
        "provider_mode": "real",
        "is_mock": False,
        "asr_fallback_used": False,
        "degradation_reasons": [],
        "events": [],
    }), encoding="utf-8")
    ui_path.write_text(json.dumps({
        "ui_coverage": "headless_chrome",
        "workbench_same_session_visible": True,
        "frontend_utterance_count": 0,
        "frontend_card_count": 0,
        "frontend_minutes_visible": False,
        "browser_console_error_count": 0,
        "network_error_count": 0,
    }), encoding="utf-8")
    artifact_root = tmp_path / "artifacts/tmp/acceptance/browser-cli-no-go"

    exit_code = tool.main([
        "--lane", "browser-live-mic",
        "--browser-mic-health-report", str(health_path),
        "--asr-probe", str(asr_path),
        "--ui-report", str(ui_path),
        "--artifact-root", str(artifact_root),
        "--run-id", "browser-cli-no-go",
    ])

    assert exit_code == 1
    manifest = json.loads((artifact_root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["audio_source"] == "browser_live_mic"
    assert manifest["browser_mic_health_status"] == "audio_capture_health_passed"
    assert manifest["browser_live_mic_go_evidence"] is False
    assert "browser_live_mic_not_proven" in manifest["degradation_reasons"]


def _write_pcm16_wav(path: Path, *, sample_rate: int = 16000, duration_seconds: float = 1.0) -> None:
    frames = int(sample_rate * duration_seconds)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        for index in range(frames):
            value = int(0.2 * 32767 * math.sin(2 * math.pi * 440 * index / sample_rate))
            handle.writeframesraw(value.to_bytes(2, byteorder="little", signed=True))
