from __future__ import annotations

from io import BytesIO
from pathlib import Path
import threading
import time
import wave

from fastapi.testclient import TestClient

from meeting_copilot_web_mvp import app as app_module
from meeting_copilot_web_mvp import audio_assets
from meeting_copilot_web_mvp.app import create_app


def _wav_bytes(*, seconds: int = 1) -> bytes:
    buffer = BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16_000)
        wav_file.writeframes(b"\x00\x00" * 16_000 * seconds)
    return buffer.getvalue()


def _configure_completed_v2_handlers(app) -> None:
    persistence = app.state.v2_persistence
    app.state.v2_correction_job_handler_impl = lambda job: {
        "job_id": job["id"],
        "changed": False,
    }
    app.state.v2_suggestion_job_handler_impl = lambda job: {
        "job_id": job["id"],
        "generated_card_count": 0,
    }
    app.state.v2_intelligence_job_handler_impl = lambda job: {
        "job_id": job["id"],
        "applied": False,
    }
    app.state.v2_post_job_handler_impls = {
        "minutes": lambda job: persistence.save_minutes(
            meeting_id=job["meeting_id"],
            job_id=job["id"],
            markdown="# 导入会议复盘\n\n已生成。",
            structured=None,
            degraded=False,
            now_ms=time.time_ns() // 1_000_000,
        ),
        "approach": lambda job: persistence.save_approach_cards(
            meeting_id=job["meeting_id"],
            job_id=job["id"],
            cards=[{"suggestion_text": "确认负责人和回滚条件"}],
            degraded=False,
            now_ms=time.time_ns() // 1_000_000,
        ),
        "index": lambda job: persistence.rebuild_search_document(
            meeting_id=job["meeting_id"],
            job_id=job["id"],
            now_ms=time.time_ns() // 1_000_000,
        ),
    }


def _wait_for_import_job(client: TestClient, meeting_id: str, status: str) -> dict:
    deadline = time.monotonic() + 5
    latest: dict | None = None
    while time.monotonic() < deadline:
        response = client.get(f"/v2/meetings/{meeting_id}/import-job")
        assert response.status_code == 200, response.text
        latest = response.json()["import_job"]
        if latest["status"] == status:
            return latest
        time.sleep(0.02)
    raise AssertionError(f"import job did not reach {status}: {latest}")


def _wait_for_v2_jobs(persistence, meeting_id: str) -> list[dict]:
    deadline = time.monotonic() + 5
    jobs: list[dict] = []
    while time.monotonic() < deadline:
        jobs = persistence.list_jobs(meeting_id=meeting_id)
        if jobs and all(job["status"] in {"succeeded", "failed", "cancelled"} for job in jobs):
            return jobs
        time.sleep(0.02)
    raise AssertionError(f"V2 jobs did not become terminal: {jobs}")


def test_import_audio_returns_202_before_transcription_and_completes_durable_pipeline(
    tmp_path,
    monkeypatch,
):
    transcript_started = threading.Event()
    allow_transcript = threading.Event()
    stage_history: list[str] = []

    monkeypatch.setattr(app_module.batch_transcribe, "is_available", lambda: True)

    def transcribe(path: Path, **_kwargs):
        transcript_started.set()
        assert allow_transcript.wait(timeout=3)
        return {
            "text": "接口先灰度百分之五，确认负责人。",
            "raw": {"provider": "funasr"},
            "audio_duration_seconds": 1.0,
            "rtf": 0.05,
        }

    monkeypatch.setattr(app_module.batch_transcribe, "transcribe_file_report", transcribe)
    monkeypatch.setattr(
        app_module.batch_transcribe,
        "ensure_wav_16k_mono",
        lambda path: path,
    )

    app = create_app(data_dir=tmp_path)
    persistence = app.state.v2_persistence
    original_update = persistence.update_import_job_stage

    def track_stage(**kwargs):
        stage_history.append(str(kwargs["stage"]))
        return original_update(**kwargs)

    monkeypatch.setattr(persistence, "update_import_job_stage", track_stage)
    _configure_completed_v2_handlers(app)

    try:
        with TestClient(app) as client:
            imported = client.post(
                "/v2/meetings/import-audio",
                files={"file": ("review.wav", _wav_bytes(), "audio/wav")},
                data={"title": "  发布 / 评审  "},
            )
            assert imported.status_code == 202, imported.text
            assert imported.headers["location"].endswith("/import-job")
            body = imported.json()
            meeting_id = body["meeting_id"]
            assert body["meeting"]["title"] == "发布 评审"
            assert body["meeting"]["title_source"] == "user"
            assert body["import_job"]["status"] == "pending"
            source_path = tmp_path / body["source_audio"]["relative_path"]
            assert source_path.read_bytes() == _wav_bytes()
            assert transcript_started.wait(timeout=1)

            allow_transcript.set()
            completed = _wait_for_import_job(client, meeting_id, "succeeded")
            _wait_for_v2_jobs(persistence, meeting_id)
            snapshot = client.get(f"/v2/meetings/{meeting_id}/snapshot")
            audio = client.get(f"/v2/meetings/{meeting_id}/audio")

        assert completed["stage"] == "completed"
        assert completed["progress"] == 100
        assert stage_history == [
            "reading",
            "normalizing",
            "transcribing",
            "correcting",
            "reviewing",
        ]
        assert snapshot.status_code == 200
        assert snapshot.json()["segments"][0]["text"] == "接口先灰度百分之五，确认负责人。"
        assert snapshot.json()["runtime"]["phase"] == "ended"
        assert snapshot.json()["minutes"]["markdown"].startswith("# 导入会议复盘")
        assert snapshot.json()["review"]["indexed"] is True
        assert audio.status_code == 200
        assert audio.json()["assembled"] is True
    finally:
        allow_transcript.set()


def test_import_audio_without_file_asr_keeps_meeting_and_managed_source(
    tmp_path,
    monkeypatch,
):
    transcribe_called = False

    monkeypatch.setattr(app_module.batch_transcribe, "is_available", lambda: False)

    def unexpected_transcribe(*_args, **_kwargs):
        nonlocal transcribe_called
        transcribe_called = True
        raise AssertionError("transcription must not run without the component")

    monkeypatch.setattr(
        app_module.batch_transcribe,
        "transcribe_file_report",
        unexpected_transcribe,
    )
    app = create_app(data_dir=tmp_path)

    with TestClient(app) as client:
        imported = client.post(
            "/v2/meetings/import-audio",
            files={"file": ("Chinese architecture review.wav", _wav_bytes(), "audio/wav")},
        )
        assert imported.status_code == 202, imported.text
        body = imported.json()
        meeting_id = body["meeting_id"]
        failed = _wait_for_import_job(client, meeting_id, "failed")
        snapshot = client.get(f"/v2/meetings/{meeting_id}/snapshot")

    assert failed["error_class"] == "file_asr_component_missing"
    assert "原始录音已保留" in failed["error_message"]
    assert failed["stage"] == "reading"
    assert transcribe_called is False
    assert body["meeting"]["title"] == "Chinese architecture review"
    assert body["meeting"]["title_source"] == "import"
    assert (tmp_path / body["source_audio"]["relative_path"]).read_bytes() == _wav_bytes()
    assert snapshot.status_code == 200
    assert snapshot.json()["import_job"]["error_class"] == "file_asr_component_missing"
    assert snapshot.json()["segments"] == []


def test_import_completion_does_not_wait_for_disconnected_review_provider(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(app_module.batch_transcribe, "is_available", lambda: True)
    monkeypatch.setattr(
        app_module.batch_transcribe,
        "transcribe_file_report",
        lambda _path, **_kwargs: {
            "text": "本地转写应先独立完成。",
            "raw": {"provider": "funasr"},
            "audio_duration_seconds": 1.0,
            "rtf": 0.05,
        },
    )
    monkeypatch.setattr(
        app_module.batch_transcribe,
        "ensure_wav_16k_mono",
        lambda path: path,
    )
    app = create_app(data_dir=tmp_path)

    def provider_disconnected(_job):
        raise app_module.ProviderRuntimeNotConfiguredDeferred()

    app.state.v2_correction_job_handler_impl = provider_disconnected
    app.state.v2_suggestion_job_handler_impl = provider_disconnected
    app.state.v2_intelligence_job_handler_impl = provider_disconnected
    app.state.v2_post_job_handler_impls = {
        "minutes": provider_disconnected,
        "approach": provider_disconnected,
        "index": provider_disconnected,
    }

    with TestClient(app) as client:
        imported = client.post(
            "/v2/meetings/import-audio",
            files={"file": ("offline-provider.wav", _wav_bytes(), "audio/wav")},
        )
        assert imported.status_code == 202, imported.text
        meeting_id = imported.json()["meeting_id"]
        completed = _wait_for_import_job(client, meeting_id, "succeeded")
        deadline = time.monotonic() + 2
        jobs = []
        while time.monotonic() < deadline:
            jobs = app.state.v2_persistence.list_jobs(meeting_id=meeting_id)
            if any(job["status"] == "retry_wait" for job in jobs):
                break
            time.sleep(0.02)

    assert completed["stage"] == "completed"
    assert completed["progress"] == 100
    assert any(job["status"] == "retry_wait" for job in jobs)
    assert {job["kind"] for job in jobs} >= {"correction", "minutes", "approach", "index"}


def test_startup_recovers_expired_running_import_job_and_finishes_it(
    tmp_path,
    monkeypatch,
):
    source_file = tmp_path / "fixture.wav"
    source_file.write_bytes(_wav_bytes())
    monkeypatch.setattr(app_module.batch_transcribe, "is_available", lambda: True)
    monkeypatch.setattr(
        app_module.batch_transcribe,
        "transcribe_file_report",
        lambda _path, **_kwargs: {
            "text": "重启后继续本地转写。",
            "raw": {"provider": "funasr"},
            "audio_duration_seconds": 1.0,
            "rtf": 0.05,
        },
    )
    monkeypatch.setattr(
        app_module.batch_transcribe,
        "ensure_wav_16k_mono",
        lambda path: path,
    )

    app = create_app(data_dir=tmp_path)
    persistence = app.state.v2_persistence
    meeting_id = "import_recovery_case"
    persistence.create_meeting(
        meeting_id=meeting_id,
        title="恢复导入",
        title_source="import",
        now_ms=1,
    )
    source_audio = audio_assets.persist_uploaded_audio_asset_from_path(
        data_dir=tmp_path,
        session_id=meeting_id,
        source_type="imported_original",
        filename="fixture.wav",
        source_path=source_file,
    )
    created = persistence.create_import_job(
        meeting_id=meeting_id,
        source_relative_path=source_audio["relative_path"],
        original_filename="fixture.wav",
        file_size_bytes=source_audio["file_size_bytes"],
        now_ms=2,
    )
    claimed = persistence.claim_import_job(
        worker_id="dead-worker",
        now_ms=3,
        lease_ms=1,
    )
    assert claimed is not None and claimed["status"] == "running"
    assert created["id"] == claimed["id"]
    _configure_completed_v2_handlers(app)

    with TestClient(app) as client:
        completed = _wait_for_import_job(client, meeting_id, "succeeded")
        snapshot = client.get(f"/v2/meetings/{meeting_id}/snapshot")

    assert completed["attempts"] == 2
    assert completed["stage"] == "completed"
    assert snapshot.json()["segments"][0]["text"] == "重启后继续本地转写。"
    assert snapshot.json()["runtime"]["phase"] == "ended"


def test_failed_import_can_be_retried_without_reuploading_the_managed_source(
    tmp_path,
    monkeypatch,
):
    available = False
    monkeypatch.setattr(app_module.batch_transcribe, "is_available", lambda: available)
    monkeypatch.setattr(
        app_module.batch_transcribe,
        "transcribe_file_report",
        lambda _path, **_kwargs: {
            "text": "原文件保留后重试成功。",
            "raw": {"provider": "funasr"},
            "audio_duration_seconds": 1.0,
            "rtf": 0.05,
        },
    )
    monkeypatch.setattr(
        app_module.batch_transcribe,
        "ensure_wav_16k_mono",
        lambda path: path,
    )
    app = create_app(data_dir=tmp_path)
    _configure_completed_v2_handlers(app)

    with TestClient(app) as client:
        imported = client.post(
            "/v2/meetings/import-audio",
            files={"file": ("retry.wav", _wav_bytes(), "audio/wav")},
        )
        assert imported.status_code == 202, imported.text
        body = imported.json()
        meeting_id = body["meeting_id"]
        failed = _wait_for_import_job(client, meeting_id, "failed")
        source_path = tmp_path / body["source_audio"]["relative_path"]
        original_payload = source_path.read_bytes()

        available = True
        retried = client.post(f"/v2/meetings/{meeting_id}/import-job/retry")
        assert retried.status_code == 202, retried.text
        assert retried.headers["location"].endswith("/import-job")
        assert retried.json()["import_job"]["status"] == "pending"
        completed = _wait_for_import_job(client, meeting_id, "succeeded")
        snapshot = client.get(f"/v2/meetings/{meeting_id}/snapshot")

    assert failed["error_class"] == "file_asr_component_missing"
    assert completed["attempts"] == 1
    assert source_path.read_bytes() == original_payload
    assert snapshot.json()["segments"][0]["text"] == "原文件保留后重试成功。"
