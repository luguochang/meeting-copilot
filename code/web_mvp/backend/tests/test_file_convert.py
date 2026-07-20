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


RUNTIME_MANIFEST_TEMPLATE = (
    Path(__file__).resolve().parents[3] / "desktop_tauri" / "runtime-bundle-manifest.json"
)


def _component_record(bundle: Path, relative: str, *, kind: str) -> dict:
    path = bundle / relative
    if kind == "file":
        payload = path.read_bytes()
        return {
            "path": relative,
            "kind": kind,
            "version": "fixture-v1",
            "size_bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
    entries = []
    for candidate in sorted(path.rglob("*")):
        if candidate.is_file():
            payload = candidate.read_bytes()
            entries.append({
                "path": candidate.relative_to(path).as_posix(),
                "kind": "file",
                "size_bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            })
    digest_payload = json.dumps(
        entries,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return {
        "path": relative,
        "kind": kind,
        "version": "fixture-v1",
        "size_bytes": sum(item["size_bytes"] for item in entries),
        "sha256": hashlib.sha256(digest_payload).hexdigest(),
        "file_count": len(entries),
        "symlink_count": 0,
    }


def _file_asr_bundle(tmp_path: Path, *, include_models: bool) -> tuple[Path, dict]:
    bundle = tmp_path / "MeetingCopilotRuntime.bundle"
    manifest = json.loads(RUNTIME_MANIFEST_TEMPLATE.read_text(encoding="utf-8"))
    manifest_path = bundle / "runtime-bundle-manifest.json"
    manifest_path.parent.mkdir(parents=True)
    launcher_relative = "bin/meeting-copilot-file-asr-python"
    manifest["runtimes"]["funasr"]["venv_executable"] = launcher_relative
    manifest["file_asr"]["runtime"]["executable"] = launcher_relative
    for relative in (
        manifest["runtimes"]["funasr"]["executable"],
        manifest["runtimes"]["funasr"]["venv_executable"],
        manifest["workers"]["file_asr"],
    ):
        path = bundle / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture", encoding="utf-8")
    runtime_root = bundle / manifest["runtimes"]["funasr"]["root"]
    runtime_root.mkdir(parents=True, exist_ok=True)
    (runtime_root / "fixture-runtime.py").write_text("fixture", encoding="utf-8")
    converter = manifest["file_asr"]["converter"]
    converter_path = bundle / converter["path"]
    converter_path.parent.mkdir(parents=True, exist_ok=True)
    converter_path.write_text("fixture-ffmpeg", encoding="utf-8")
    converter_license = bundle / converter["license_path"]
    converter_license.parent.mkdir(parents=True, exist_ok=True)
    converter_license.write_text("fixture-license", encoding="utf-8")
    if include_models:
        for model in manifest["file_asr"]["models"].values():
            for relative in model["required_files"]:
                path = bundle / model["root"] / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("fixture", encoding="utf-8")

    runtime_record = _component_record(
        bundle,
        manifest["runtimes"]["funasr"]["root"],
        kind="directory",
    )
    launcher_record = _component_record(bundle, launcher_relative, kind="file")
    worker_relative = manifest["workers"]["file_asr"]
    worker_record = _component_record(bundle, worker_relative, kind="file")
    converter_record = _component_record(bundle, converter["path"], kind="file")
    manifest["runtimes"]["funasr"].update({
        "size_bytes": runtime_record["size_bytes"],
        "sha256": runtime_record["sha256"],
    })
    manifest["file_asr"]["runtime"].update({
        "size_bytes": runtime_record["size_bytes"],
        "sha256": runtime_record["sha256"],
    })
    manifest["worker_inventory"]["file_asr"].update({
        "path": worker_relative,
        "size_bytes": worker_record["size_bytes"],
        "sha256": worker_record["sha256"],
    })
    manifest["file_asr"]["worker"].update({
        "path": worker_relative,
        "size_bytes": worker_record["size_bytes"],
        "sha256": worker_record["sha256"],
    })
    converter.update({
        "size_bytes": converter_record["size_bytes"],
        "sha256": converter_record["sha256"],
    })
    components = {
        "shared_asr.runtime": runtime_record,
        "file_asr.python_launcher": launcher_record,
        "file_asr.worker": worker_record,
        "file_asr.converter": converter_record,
    }
    if include_models:
        for name, model in manifest["file_asr"]["models"].items():
            record = _component_record(bundle, model["root"], kind="directory")
            model.update({
                "size_bytes": record["size_bytes"],
                "sha256": record["sha256"],
            })
            components[f"file_asr.model.{name}"] = record | {"model_id": model["model_id"]}
    manifest["component_inventory"] = {
        "schema_version": "meeting_copilot.runtime_component_inventory.v1",
        "status": "sealed",
        "components": components,
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path, manifest


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
    monkeypatch.setattr(batch_transcribe, "_ffmpeg_packaged_mode", None)
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", None)
    monkeypatch.setattr(batch_transcribe, "shutil", shutil, raising=False)
    monkeypatch.setattr(
        batch_transcribe.shutil,
        "which",
        lambda name: "/opt/homebrew/bin/ffmpeg" if name == "ffmpeg" else None,
    )

    assert batch_transcribe._get_ffmpeg() == "/opt/homebrew/bin/ffmpeg"


def test_get_ffmpeg_falls_back_to_system_when_development_imageio_path_is_forbidden(monkeypatch):
    import imageio_ffmpeg

    monkeypatch.setattr(batch_transcribe, "_ffmpeg_path", None)
    monkeypatch.setattr(batch_transcribe, "_ffmpeg_packaged_mode", None)
    monkeypatch.setattr(
        imageio_ffmpeg,
        "get_ffmpeg_exe",
        lambda: "/tmp/meeting-copilot/.venv/site-packages/imageio_ffmpeg/ffmpeg",
    )
    monkeypatch.setattr(
        batch_transcribe.shutil,
        "which",
        lambda name: "/opt/homebrew/bin/ffmpeg" if name == "ffmpeg" else None,
    )

    assert batch_transcribe._get_ffmpeg(packaged_mode=False) == "/opt/homebrew/bin/ffmpeg"


def test_get_ffmpeg_never_falls_back_to_a_system_binary_in_packaged_mode(monkeypatch, tmp_path):
    import imageio_ffmpeg

    monkeypatch.setattr(batch_transcribe, "_ffmpeg_path", None)
    monkeypatch.setattr(batch_transcribe, "_ffmpeg_packaged_mode", None)
    monkeypatch.setattr(
        imageio_ffmpeg,
        "get_ffmpeg_exe",
        lambda: "/opt/homebrew/bin/ffmpeg",
    )
    monkeypatch.setattr(batch_transcribe.shutil, "which", lambda _name: "/opt/homebrew/bin/ffmpeg")

    assert batch_transcribe._get_ffmpeg(packaged_mode=True, bundle_root=tmp_path) is None


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
        "build_minutes_artifact",
        lambda transcript, config: (
            "# 会议纪要\n\n- 先灰度 5%\n",
            {
                "background": "接口灰度发布",
                "decisions": ["先灰度 5%"],
                "action_items": [],
                "risks": [],
                "open_questions": [],
                "evidence_quotes": ["接口先灰度 5%"],
            },
            {"total_tokens": 12},
            False,
        ),
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
    manifest_path, manifest = _file_asr_bundle(tmp_path, include_models=True)
    bundle = manifest_path.parent
    fake_python = bundle / manifest["runtimes"]["funasr"]["venv_executable"]
    fake_worker = bundle / manifest["workers"]["file_asr"]
    fake_model = bundle / manifest["file_asr"]["models"]["offline"]["root"]
    fake_vad = bundle / manifest["file_asr"]["models"]["vad"]["root"]
    fake_punc = bundle / manifest["file_asr"]["models"]["punc"]["root"]
    monkeypatch.setenv("MEETING_COPILOT_RUNTIME_MANIFEST", str(manifest_path))
    packaged_override_names = [
        "MEETING_COPILOT_BATCH_FUNASR_PYTHON",
        "MEETING_COPILOT_BATCH_TRANSCRIBE_WORKER",
        "MEETING_COPILOT_FILE_ASR_MODEL_DIR",
        "MEETING_COPILOT_FILE_ASR_VAD_MODEL_DIR",
        "MEETING_COPILOT_FILE_ASR_PUNC_MODEL_DIR",
        "MEETING_COPILOT_FFMPEG",
        "IMAGEIO_FFMPEG_EXE",
    ]
    for name in packaged_override_names:
        monkeypatch.setenv(name, f"/external/{name.lower()}")
    monkeypatch.setattr(batch_transcribe, "ensure_wav_16k_mono", lambda path: path)
    calls = []
    child_environments = []
    monkeypatch.setenv("PYTHONHOME", "/backend/python")
    monkeypatch.setenv("PYTHONPATH", "/backend/site-packages")

    def fake_run(cmd, capture_output, text, timeout, env):
        calls.append(cmd)
        child_environments.append(env)
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
    assert cmd[2] == str(audio)
    assert "--offline-batch" in cmd
    assert cmd[cmd.index("--model") + 1] == str(fake_model)
    assert cmd[cmd.index("--vad-model") + 1] == str(fake_vad)
    assert cmd[cmd.index("--punc-model") + 1] == str(fake_punc)
    assert "PYTHONHOME" not in child_environments[0]
    assert "PYTHONPATH" not in child_environments[0]
    assert all(name not in child_environments[0] for name in packaged_override_names)
    assert child_environments[0]["HF_HUB_OFFLINE"] == "1"
    assert child_environments[0]["TRANSFORMERS_OFFLINE"] == "1"


def test_packaged_runtime_ignores_external_component_and_converter_overrides(tmp_path):
    manifest_path, manifest = _file_asr_bundle(tmp_path, include_models=True)
    external = tmp_path / "external-runtime"
    external_paths = {
        "MEETING_COPILOT_BATCH_FUNASR_PYTHON": external / "python",
        "MEETING_COPILOT_BATCH_TRANSCRIBE_WORKER": external / "worker.py",
        "MEETING_COPILOT_FILE_ASR_MODEL_DIR": external / "offline",
        "MEETING_COPILOT_FILE_ASR_VAD_MODEL_DIR": external / "vad",
        "MEETING_COPILOT_FILE_ASR_PUNC_MODEL_DIR": external / "punc",
        "MEETING_COPILOT_FFMPEG": external / "ffmpeg",
        "IMAGEIO_FFMPEG_EXE": external / "imageio-ffmpeg",
    }
    for name, path in external_paths.items():
        if "MODEL_DIR" in name:
            path.mkdir(parents=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("hijacked", encoding="utf-8")
    environ = {
        "MEETING_COPILOT_RUNTIME_MANIFEST": str(manifest_path),
        **{name: str(path) for name, path in external_paths.items()},
    }

    runtime = batch_transcribe._resolve_runtime_components(environ)

    inventory = manifest["component_inventory"]["components"]
    expected_inventory_names = {
        "funasr_python": "file_asr.python_launcher",
        "worker": "file_asr.worker",
        "offline_model": "file_asr.model.offline",
        "vad_model": "file_asr.model.vad",
        "punc_model": "file_asr.model.punc",
        "converter": "file_asr.converter",
    }
    for name, inventory_name in expected_inventory_names.items():
        component = runtime["components"][name]
        assert component["source"] == "sealed_manifest"
        assert component["path"] == manifest_path.parent / inventory[inventory_name]["path"]
        assert external not in component["path"].parents

    capability = batch_transcribe.capability_status(environ)
    assert capability["status"] == "ready"
    assert capability["components"]["ffmpeg"]["status"] == "ready"
    assert capability["components"]["ffmpeg"]["source"] == "bundled"


def test_packaged_runtime_rejects_unsealed_component_inventory(tmp_path):
    manifest_path, manifest = _file_asr_bundle(tmp_path, include_models=True)
    manifest["component_inventory"]["status"] = "unsealed"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    capability = batch_transcribe.capability_status(
        {"MEETING_COPILOT_RUNTIME_MANIFEST": str(manifest_path)}
    )

    assert capability["status"] == "invalid_runtime_configuration"
    assert capability["available"] is False
    assert capability["manifest_errors"] == ["runtime_component_inventory_not_sealed"]


def test_packaged_runtime_rejects_manifest_component_path_mismatch(tmp_path):
    manifest_path, manifest = _file_asr_bundle(tmp_path, include_models=True)
    manifest["workers"]["file_asr"] = manifest["runtimes"]["funasr"]["venv_executable"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    capability = batch_transcribe.capability_status(
        {"MEETING_COPILOT_RUNTIME_MANIFEST": str(manifest_path)}
    )

    assert capability["status"] == "invalid_runtime_configuration"
    assert capability["components"]["worker"]["status"] == "invalid"
    assert capability["components"]["worker"]["reason"] == "manifest_component_path_mismatch"


def test_packaged_runtime_rejects_manifest_component_hash_mismatch(tmp_path):
    manifest_path, manifest = _file_asr_bundle(tmp_path, include_models=True)
    manifest["file_asr"]["worker"]["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    capability = batch_transcribe.capability_status(
        {"MEETING_COPILOT_RUNTIME_MANIFEST": str(manifest_path)}
    )

    assert capability["status"] == "invalid_runtime_configuration"
    assert capability["components"]["worker"]["status"] == "invalid"
    assert capability["components"]["worker"]["reason"] == "manifest_component_hash_mismatch"


def test_batch_runtime_preserves_the_funasr_venv_symlink_for_execution(tmp_path):
    manifest_path, manifest = _file_asr_bundle(tmp_path, include_models=True)
    bundle = manifest_path.parent
    base_python = bundle / manifest["runtimes"]["funasr"]["executable"]
    venv_python = bundle / manifest["runtimes"]["funasr"]["venv_executable"]
    venv_python.unlink()
    venv_python.symlink_to(Path("..") / base_python.relative_to(bundle))

    runtime = batch_transcribe._resolve_runtime_components(
        {"MEETING_COPILOT_RUNTIME_MANIFEST": str(manifest_path)}
    )

    assert runtime["components"]["funasr_python"]["path"] == venv_python
    assert runtime["components"]["funasr_python"]["configuration_errors"] == []
    assert runtime["components"]["funasr_python"]["path"].resolve() == base_python.resolve()


def test_batch_runtime_manifest_reports_concrete_missing_model_components(tmp_path):
    manifest_path, _manifest = _file_asr_bundle(tmp_path, include_models=False)

    capability = batch_transcribe.capability_status(
        {"MEETING_COPILOT_RUNTIME_MANIFEST": str(manifest_path)}
    )

    assert capability["status"] == "file_asr_models_not_installed"
    assert capability["available"] is False
    assert capability["missing_components"] == [
        "offline_model",
        "vad_model",
        "punc_model",
    ]
    assert capability["components"]["funasr_python"]["status"] == "ready"
    assert capability["components"]["worker"]["status"] == "ready"
    assert capability["network_offline"] is None
    assert capability["remote_asr_used"] is False


def test_batch_runtime_env_resolves_all_components_and_supported_formats(tmp_path):
    paths = {
        "MEETING_COPILOT_BATCH_FUNASR_PYTHON": tmp_path / "python",
        "MEETING_COPILOT_BATCH_TRANSCRIBE_WORKER": tmp_path / "transcribe_funasr.py",
        "MEETING_COPILOT_FILE_ASR_MODEL_DIR": tmp_path / "offline",
        "MEETING_COPILOT_FILE_ASR_VAD_MODEL_DIR": tmp_path / "vad",
        "MEETING_COPILOT_FILE_ASR_PUNC_MODEL_DIR": tmp_path / "punc",
        "MEETING_COPILOT_FFMPEG": tmp_path / "ffmpeg",
    }
    for key, path in paths.items():
        if "MODEL_DIR" in key:
            path.mkdir()
        else:
            path.write_text("fixture", encoding="utf-8")
    capability = batch_transcribe.capability_status(
        {key: str(path) for key, path in paths.items()}
    )

    assert capability["status"] == "ready"
    assert capability["available"] is True
    assert capability["supported_import_formats"] == [
        ".wav",
        ".mp3",
        ".m4a",
        ".aac",
        ".flac",
        ".mp4",
        ".mov",
    ]
    assert all(item["available"] for item in capability["formats"].values())
    assert batch_transcribe.import_format_capability("meeting.mkv", {
        key: str(path) for key, path in paths.items()
    })["status"] == "unsupported_import_format"


def test_batch_runtime_rejects_user_model_cache_paths(tmp_path):
    python = tmp_path / "python"
    worker = tmp_path / "worker.py"
    python.write_text("fixture", encoding="utf-8")
    worker.write_text("fixture", encoding="utf-8")
    cache_root = tmp_path / ".cache" / "modelscope" / "models"
    for name in ("offline", "vad", "punc"):
        (cache_root / name).mkdir(parents=True)
    capability = batch_transcribe.capability_status(
        {
            "MEETING_COPILOT_BATCH_FUNASR_PYTHON": str(python),
            "MEETING_COPILOT_BATCH_TRANSCRIBE_WORKER": str(worker),
            "MEETING_COPILOT_FILE_ASR_MODEL_DIR": str(cache_root / "offline"),
            "MEETING_COPILOT_FILE_ASR_VAD_MODEL_DIR": str(cache_root / "vad"),
            "MEETING_COPILOT_FILE_ASR_PUNC_MODEL_DIR": str(cache_root / "punc"),
        }
    )

    assert capability["status"] == "invalid_runtime_configuration"
    assert capability["invalid_components"] == ["offline_model", "vad_model", "punc_model"]
    assert all(
        capability["components"][name]["reason"] == "development_or_user_cache_path_forbidden"
        for name in capability["invalid_components"]
    )
