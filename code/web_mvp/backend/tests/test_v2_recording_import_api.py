from __future__ import annotations

from io import BytesIO
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
import time
import wave
import pytest

from fastapi.testclient import TestClient

from meeting_copilot_web_mvp import app as app_module
from meeting_copilot_web_mvp.asr_refiner import RefinementResult
from meeting_copilot_web_mvp import audio_assets
from meeting_copilot_web_mvp import asr_resource_guard
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


def test_import_audio_splits_long_transcript_into_timed_semantic_segments(tmp_path, monkeypatch):
    transcript = (
        "第一部分确认中文实时识别需要保留原文，并在句尾使用本地模型完成精修。"
        "第二部分讨论断线恢复，恢复后继续同一场会议，但写入新的录音批次。"
        "第三部分明确会议纪要失败不能影响录音、正文和其他会后产物。"
    )
    monkeypatch.setattr(app_module.batch_transcribe, "is_available", lambda: True)
    monkeypatch.setattr(
        app_module.batch_transcribe,
        "transcribe_file_report",
        lambda _path, **_kwargs: {
            "text": transcript,
            "raw": {"provider": "funasr"},
            "audio_duration_seconds": 9.0,
            "rtf": 0.05,
        },
    )
    monkeypatch.setattr(app_module.batch_transcribe, "ensure_wav_16k_mono", lambda path: path)
    app = create_app(data_dir=tmp_path)
    _configure_completed_v2_handlers(app)

    with TestClient(app) as client:
        imported = client.post(
            "/v2/meetings/import-audio",
            files={"file": ("long.wav", _wav_bytes(seconds=9), "audio/wav")},
        )
        assert imported.status_code == 202, imported.text
        meeting_id = imported.json()["meeting_id"]
        _wait_for_import_job(client, meeting_id, "succeeded")
        snapshot = client.get(f"/v2/meetings/{meeting_id}/snapshot").json()

    segments = snapshot["segments"]
    assert len(segments) == 3
    assert [segment["segment_id"] for segment in segments] == [
        "import_seg_0001",
        "import_seg_0002",
        "import_seg_0003",
    ]
    assert "".join(segment["normalized_text"] for segment in segments) == transcript
    assert segments[0]["started_at_ms"] == 0
    assert segments[-1]["ended_at_ms"] == 9_000
    assert all(
        left["ended_at_ms"] == right["started_at_ms"]
        for left, right in zip(segments, segments[1:])
    )


def test_import_audio_without_file_asr_keeps_meeting_and_managed_source(
    tmp_path,
    monkeypatch,
):
    transcribe_called = False

    monkeypatch.setattr(app_module.batch_transcribe, "is_available", lambda: False)
    monkeypatch.setattr(
        app_module.asr_refiner,
        "refinement_capability",
        lambda: {"status": "unavailable"},
    )

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


def test_import_audio_reuses_resident_offline_refiner_when_batch_runtime_is_missing(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(asr_resource_guard, "check_capacity", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(app_module.batch_transcribe, "is_available", lambda: False)
    monkeypatch.setattr(
        app_module.asr_refiner,
        "refinement_capability",
        lambda: {"status": "ready", "model_id": "offline"},
    )
    monkeypatch.setattr(app_module.batch_transcribe, "ensure_wav_16k_mono", lambda path: path)
    monkeypatch.setattr(
        app_module.asr_refiner,
        "refine_wav_file",
        lambda _path, **_kwargs: RefinementResult(
            text="本地常驻模型完成录音导入转写。",
            status="refined",
            model_id="offline",
        ),
    )
    app = create_app(data_dir=tmp_path)
    _configure_completed_v2_handlers(app)

    with TestClient(app) as client:
        imported = client.post(
            "/v2/meetings/import-audio",
            files={"file": ("fallback.wav", _wav_bytes(), "audio/wav")},
        )
        assert imported.status_code == 202, imported.text
        meeting_id = imported.json()["meeting_id"]
        completed = _wait_for_import_job(client, meeting_id, "succeeded")
        snapshot = client.get(f"/v2/meetings/{meeting_id}/snapshot").json()

    assert completed["stage"] == "completed"
    assert snapshot["segments"][0]["normalized_text"] == "本地常驻模型完成录音导入转写。"
    assert snapshot["runtime"]["phase"] == "ended"


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
        app_module.asr_refiner,
        "refinement_capability",
        lambda: {"status": "unavailable"},
    )
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


@pytest.mark.parametrize("code", [
    "asr_capacity_unavailable", "asr_resource_probe_failed",
    "asr_resource_guard_triggered", "asr_cold_start_busy",
])
def test_resource_failure_is_terminal_keeps_recording_and_requires_manual_retry(
    tmp_path, monkeypatch, code,
):
    attempts = []
    monkeypatch.setattr(app_module.batch_transcribe, "is_available", lambda: True)

    def refuse(path, **_kwargs):
        attempts.append(path)
        raise asr_resource_guard.AsrResourceError(code)

    monkeypatch.setattr(app_module.batch_transcribe, "transcribe_file_report", refuse)
    app = create_app(data_dir=tmp_path)
    with TestClient(app) as client:
        response = client.post(
            "/v2/meetings/import-audio",
            files={"file": ("protected.wav", _wav_bytes(), "audio/wav")},
        )
        assert response.status_code == 202
        body = response.json()
        meeting_id = body["meeting_id"]
        failed = _wait_for_import_job(client, meeting_id, "failed")
        assert failed["error_class"] == code
        assert "原始录音已保留" in failed["error_message"]
        assert "手动重试" in failed["error_message"]
        assert failed["attempts"] == 1
        # Even a claim far after the automatic retry delay cannot restart it.
        assert app.state.v2_persistence.claim_import_job(
            worker_id="check-no-auto-retry", now_ms=failed["updated_at_ms"] + 60_000,
            lease_ms=30_000,
        ) is None
        for _ in range(3):
            assert client.get(f"/v2/meetings/{meeting_id}/import-job").status_code == 200
        assert app.state.v2_persistence.list_jobs(meeting_id=meeting_id) == []
        assert (tmp_path / body["source_audio"]["relative_path"]).read_bytes() == _wav_bytes()
        assert len(attempts) == 1


def test_explicit_import_retry_unlocks_only_one_refiner_attempt(tmp_path, monkeypatch):
    attempts = []
    monkeypatch.setattr(asr_resource_guard, "check_capacity", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(app_module.batch_transcribe, "is_available", lambda: False)
    monkeypatch.setattr(app_module.batch_transcribe, "ensure_wav_16k_mono", lambda path: path)
    monkeypatch.setattr(app_module.asr_refiner, "refinement_capability", lambda: {"status": "ready"})

    def refine(_path, **kwargs):
        attempts.append(kwargs.get("retry_failed", False))
        return RefinementResult(text="", status="failed", reason="asr_capacity_unavailable")

    monkeypatch.setattr(app_module.asr_refiner, "refine_wav_file", refine)
    app = create_app(data_dir=tmp_path)
    with TestClient(app) as client:
        body = client.post(
            "/v2/meetings/import-audio",
            files={"file": ("latch.wav", _wav_bytes(), "audio/wav")},
        ).json()
        meeting_id = body["meeting_id"]
        first = _wait_for_import_job(client, meeting_id, "failed")
        assert first["error_class"] == "asr_capacity_unavailable"
        assert attempts == [False]
        assert client.post(f"/v2/meetings/{meeting_id}/import-job/retry").status_code == 202
        second = _wait_for_import_job(client, meeting_id, "failed")
        assert second["error_class"] == "asr_capacity_unavailable"
        assert attempts == [False, True]
        another = client.post(
            "/v2/meetings/import-audio",
            files={"file": ("unrelated.wav", _wav_bytes(), "audio/wav")},
        ).json()
        _wait_for_import_job(client, another["meeting_id"], "failed")
        assert attempts == [False, True, False]
        assert (tmp_path / body["source_audio"]["relative_path"]).read_bytes() == _wav_bytes()


def test_fallback_capacity_refusal_happens_before_normalization(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module.batch_transcribe, "is_available", lambda: False)
    monkeypatch.setattr(app_module.asr_refiner, "refinement_capability", lambda: {"status": "ready"})

    def refuse(*_args, **_kwargs):
        raise asr_resource_guard.AsrResourceError("asr_resource_probe_failed")

    def unexpected(*_args, **_kwargs):
        raise AssertionError("no converter or model starts after failed resource probe")

    monkeypatch.setattr(asr_resource_guard, "check_capacity", refuse)
    monkeypatch.setattr(app_module.batch_transcribe, "ensure_wav_16k_mono", unexpected)
    monkeypatch.setattr(app_module.asr_refiner, "refine_wav_file", unexpected)
    with TestClient(create_app(data_dir=tmp_path)) as client:
        body = client.post(
            "/v2/meetings/import-audio",
            files={"file": ("no-probe.wav", _wav_bytes(), "audio/wav")},
        ).json()
        failed = _wait_for_import_job(client, body["meeting_id"], "failed")
        assert failed["error_class"] == "asr_resource_probe_failed"
        assert (tmp_path / body["source_audio"]["relative_path"]).read_bytes() == _wav_bytes()


def test_online_prewarm_finishes_before_explicit_offline_preparation(tmp_path, monkeypatch):
    events = []
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_REFINER_POLICY", "prewarm")
    monkeypatch.setattr(app_module.asr_refiner, "refinement_capability", lambda: {"status": "ready"})

    def online():
        events.append("online-start")
        time.sleep(0.1)
        events.append("online-ready")
        return True

    def offline(**kwargs):
        assert events == ["online-start", "online-ready"]
        assert kwargs.get("retry_failed", False) is True
        events.append("offline-capacity-check")
        return False

    monkeypatch.setattr(app_module.asr_stream, "prewarm_funasr_resident_manager", online)
    monkeypatch.setattr(app_module.asr_refiner, "prewarm_refiner_worker", offline)
    monkeypatch.setattr(app_module.asr_stream, "shutdown_funasr_resident_manager", lambda: None)
    monkeypatch.setattr(app_module.asr_refiner, "shutdown_refiner_worker", lambda: None)
    with TestClient(create_app(data_dir=tmp_path, prewarm_funasr=True)) as client:
        assert client.get("/health").status_code == 200
        assert client.app.state.funasr_resident_prewarm_ready is True
        assert client.app.state.funasr_refiner_prewarm_ready is False
        assert events == ["online-start", "online-ready"]
        preparation = client.post("/providers/asr/prewarm", headers={
            "origin": "http://127.0.0.1", "x-meeting-copilot-verification": "1",
        })
        assert preparation.status_code == 503  # The controlled preparation is refused, never marked ready.
    assert events == ["online-start", "online-ready", "offline-capacity-check"]


def test_concurrent_import_retries_preserve_first_recovery_authorization(tmp_path, monkeypatch):
    attempts = []
    retry_entered = threading.Event()
    allow_retry = threading.Event()
    retries = []
    monkeypatch.setattr(asr_resource_guard, "check_capacity", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(app_module.batch_transcribe, "is_available", lambda: False)
    monkeypatch.setattr(app_module.batch_transcribe, "ensure_wav_16k_mono", lambda path: path)
    monkeypatch.setattr(app_module.asr_refiner, "refinement_capability", lambda: {"status": "ready"})

    def refine(_path, **kwargs):
        attempts.append(kwargs.get("retry_failed", False))
        return RefinementResult(text="", status="failed", reason="asr_capacity_unavailable")

    monkeypatch.setattr(app_module.asr_refiner, "refine_wav_file", refine)
    app = create_app(data_dir=tmp_path)
    original_retry = app.state.v2_persistence.retry_import_job

    def delayed_retry(**kwargs):
        retries.append(kwargs)
        assert len(retries) == 1, "a concurrent rejected retry must not reach persistence"
        retry_entered.set()
        assert allow_retry.wait(3)
        return original_retry(**kwargs)

    monkeypatch.setattr(app.state.v2_persistence, "retry_import_job", delayed_retry)
    try:
        with TestClient(app) as client:
            body = client.post(
                "/v2/meetings/import-audio",
                files={"file": ("concurrent.wav", _wav_bytes(), "audio/wav")},
            ).json()
            meeting_id = body["meeting_id"]
            _wait_for_import_job(client, meeting_id, "failed")
            with ThreadPoolExecutor(max_workers=1) as executor:
                first = executor.submit(client.post, f"/v2/meetings/{meeting_id}/import-job/retry")
                try:
                    assert retry_entered.wait(2)
                    second = client.post(f"/v2/meetings/{meeting_id}/import-job/retry")
                    assert second.status_code == 409
                    assert len(retries) == 1
                finally:
                    allow_retry.set()
                assert first.result(3).status_code == 202
            _wait_for_import_job(client, meeting_id, "failed")
            assert attempts == [False, True]
            assert (tmp_path / body["source_audio"]["relative_path"]).read_bytes() == _wav_bytes()
    finally:
        allow_retry.set()
