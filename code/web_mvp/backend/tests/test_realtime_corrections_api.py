from concurrent.futures import ThreadPoolExecutor
import json
from threading import Barrier, Event
import time

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from meeting_copilot_web_mvp import app as app_module, asr_correct, asr_stream, llm_service
from meeting_copilot_web_mvp.app import create_app
from meeting_copilot_web_mvp.sqlite_repository import SqliteAsrLiveSessionRepository


class _CorrectionRecognizer:
    provider = "test_contract_realtime_asr"
    provider_mode = "real"
    is_mock = False
    fallback_used = False
    degradation_reasons = []

    def __init__(self, session_id: str, text: str):
        self.session_id = session_id
        self.text = text
        self._seq = 0

    def recognize_chunk(self, pcm):
        self._seq += 1
        return [
            {
                "event_type": "final",
                "segment_id": "corr_seg_1",
                "text": self.text,
                "start_ms": 0,
                "end_ms": self._seq * 300,
                "confidence": 0.92,
            }
        ]

    def finalize(self):
        return []


class _TwoSegmentCorrectionRecognizer(_CorrectionRecognizer):
    def recognize_chunk(self, pcm):
        return [
            {
                "event_type": "final",
                "segment_id": "corr_seg_1",
                "text": "接口先恢度百分之五。",
                "start_ms": 0,
                "end_ms": 300,
                "confidence": 0.92,
            },
            {
                "event_type": "final",
                "segment_id": "corr_seg_2",
                "text": "错误率超过百分之零点一就回滚。",
                "start_ms": 300,
                "end_ms": 600,
                "confidence": 0.92,
            },
        ]


def _configure_llm(monkeypatch):
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gw.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "gpt-5.5")
    monkeypatch.setenv("LLM_GATEWAY_PROVIDER_LABEL", "team_gateway")
    monkeypatch.delenv("LLM_GATEWAY_IS_MOCK", raising=False)


def _start_live_session(monkeypatch, client: TestClient, session_id: str, text: str):
    monkeypatch.setattr(asr_stream, "get_recognizer", lambda sid: _CorrectionRecognizer(sid, text))
    websocket = client.websocket_connect(f"/live/asr/stream/ws/{session_id}?audio_source=browser_live_mic")
    ws = websocket.__enter__()
    ws.send_bytes(b"\x00" * 3200)
    event = json.loads(ws.receive_text())
    assert event["event_type"] == "final"
    return websocket, ws


def _receive_final_events(ws, count: int):
    finals = []
    while len(finals) < count:
        event = json.loads(ws.receive_text())
        if event.get("event_type") == "final":
            finals.append(event)
    return finals


def _enable_l2(client: TestClient) -> None:
    settings = client.get("/settings").json()
    settings["asr"]["l2_correction_enabled"] = True
    assert client.patch("/settings", json=settings).status_code == 200


def test_realtime_correction_allows_persisted_finals_after_stream_tail_interrupt():
    record = {
        "session_id": "interrupted-final",
        "source": "live_asr_stream",
        "trace_kind": "live_event",
        "provider": "funasr_realtime",
        "provider_mode": "real",
        "is_mock": False,
        "input_source": "browser_live_mic",
        "ingest_mode": "live_asr_stream",
        "asr_fallback_used": False,
        "degradation_reasons": ["stream_interrupted"],
        "events": [
            {
                "event_type": "transcript_final",
                "payload": {
                    "segment_id": "segment-1",
                    "text": "已经持久化的会议文字。",
                },
            }
        ],
    }

    assert app_module._realtime_correction_blockers(record) == []


def test_realtime_correction_setting_fails_closed_before_provider_and_updates_existing_session(
    monkeypatch,
    tmp_path,
):
    _configure_llm(monkeypatch)
    config_calls = []
    provider_calls = []

    def disabled_config(cls):
        config_calls.append("from_env")
        return None

    monkeypatch.setattr(
        asr_correct,
        "correct_transcript",
        lambda raw, cfg, **kwargs: provider_calls.append(raw),
    )
    client = TestClient(create_app(data_dir=tmp_path))
    context, ws = _start_live_session(
        monkeypatch,
        client,
        "correction_dynamic_setting",
        "接口先灰度 5%，如果错误率超过 0.1% 就回滚，owner 张三今天补 SLO 看板。",
    )
    try:
        disabled_settings = client.get("/settings").json()
        disabled_settings["asr"]["l2_correction_enabled"] = False
        assert client.patch("/settings", json=disabled_settings).status_code == 200
        monkeypatch.setattr(
            llm_service.LlmConfig,
            "from_env",
            classmethod(disabled_config),
        )
        disabled = client.post(
            "/live/asr/sessions/correction_dynamic_setting/realtime-corrections/run-once",
            json={"force": True},
        )

        assert disabled.status_code == 200
        assert disabled.json()["called"] is False
        assert disabled.json()["gate"] == {
            "eligible": False,
            "reason": "disabled_by_setting",
            "policy_version": "realtime-transcript-correction.v1",
        }
        assert "error" not in disabled.json()
        assert config_calls == []
        assert provider_calls == []

        enabled_settings = client.get("/settings").json()
        enabled_settings["asr"]["l2_correction_enabled"] = True
        assert client.patch("/settings", json=enabled_settings).status_code == 200
        config = llm_service.LlmConfig(
            base_url="https://gw.example",
            api_key="sk-test",
            model="gpt-5.5",
        )
        monkeypatch.setattr(
            llm_service.LlmConfig,
            "from_env",
            classmethod(lambda cls: config_calls.append("from_env") or config),
        )
        monkeypatch.setattr(
            asr_correct,
            "correct_transcript",
            lambda raw, cfg, **kwargs: (
                provider_calls.append(raw) or raw,
                {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                False,
            ),
        )

        enabled = client.post(
            "/live/asr/sessions/correction_dynamic_setting/realtime-corrections/run-once",
            json={"force": True},
        )

        assert enabled.status_code == 200
        assert enabled.json()["called"] is True
        assert config_calls == ["from_env"]
        assert len(provider_calls) == 1
        assert "接口先灰度 5%" in provider_calls[0]
    finally:
        ws.send_text("END")
        context.__exit__(None, None, None)


def test_realtime_correction_gate_closed_makes_zero_llm_calls(monkeypatch, tmp_path):
    _configure_llm(monkeypatch)
    calls = []
    monkeypatch.setattr(asr_correct, "correct_transcript", lambda raw, cfg, **kwargs: calls.append(raw))
    client = TestClient(create_app(data_dir=tmp_path))
    _enable_l2(client)
    context, ws = _start_live_session(
        monkeypatch,
        client,
        "correction_gate_closed",
        "接口先恢度百分之五，如果错误率超过百分之零点一就回滚。",
    )
    try:
        response = client.post(
            "/live/asr/sessions/correction_gate_closed/realtime-corrections/run-once",
            json={"force": False},
        )
        assert response.status_code == 200
        assert response.json()["called"] is False
        assert response.json()["gate"]["reason"] == "batch_gate_closed"
        assert response.json()["transcript_revisions"] == []
        assert calls == []
    finally:
        ws.send_text("END")
        context.__exit__(None, None, None)


def test_realtime_correction_interval_opens_without_new_asr_event(monkeypatch, tmp_path):
    _configure_llm(monkeypatch)
    now_seconds = {"value": 1_000.0}
    monkeypatch.setattr(app_module.time, "time", lambda: now_seconds["value"])
    calls = []

    def correct(raw, cfg, **kwargs):
        calls.append(raw)
        return raw.replace("恢度", "灰度"), {"total_tokens": 3}, False

    monkeypatch.setattr(asr_correct, "correct_transcript", correct)
    client = TestClient(create_app(data_dir=tmp_path))
    _enable_l2(client)
    context, ws = _start_live_session(
        monkeypatch,
        client,
        "correction_interval",
        "接口先恢度百分之五，如果错误率超过百分之零点一就回滚。",
    )
    try:
        first = client.post(
            "/live/asr/sessions/correction_interval/realtime-corrections/run-once",
            json={"force": False},
        )
        assert first.status_code == 200
        assert first.json()["called"] is False
        assert first.json()["gate"]["retry_after_ms"] == 15_000

        now_seconds["value"] += 30.0
        second = client.post(
            "/live/asr/sessions/correction_interval/realtime-corrections/run-once",
            json={"force": False},
        )

        assert second.status_code == 200
        assert second.json()["called"] is True
        assert second.json()["gate"]["reason"] == "min_interval_reached"
        assert len(calls) == 1
    finally:
        ws.send_text("END")
        context.__exit__(None, None, None)


def test_realtime_correction_force_calls_once_and_persists_revision(monkeypatch, tmp_path):
    _configure_llm(monkeypatch)
    calls = []

    def correct(raw, cfg, **kwargs):
        calls.append(raw)
        persisted = SqliteAsrLiveSessionRepository(tmp_path).get("correction_force")
        reservation = persisted["realtime_transcript_correction"]["reservation"]
        assert reservation["status"] == "reserved"
        assert reservation["segment_ids"] == ["corr_seg_1"]
        assert reservation["started_at_ms"] > 0
        assert reservation["lease_expires_at_ms"] > reservation["started_at_ms"]
        assert reservation["retry_count"] == 0
        assert reservation["batch_id"]
        corrected = raw.replace("灰度百分之五", "灰度 5%").replace("百分之零点一", "0.1%")
        corrected = corrected.replace("错误率超过0.1%就", "错误率超过 0.1% 就")
        return (
            corrected,
            {
                "prompt_tokens": 20,
                "completion_tokens": 10,
                "total_tokens": 30,
            },
            False,
        )

    monkeypatch.setattr(asr_correct, "correct_transcript", correct)
    client = TestClient(create_app(data_dir=tmp_path))
    _enable_l2(client)
    context, ws = _start_live_session(
        monkeypatch,
        client,
        "correction_force",
        "接口先恢度百分之五，如果错误率超过百分之零点一就回滚。",
    )
    try:
        response = client.post(
            "/live/asr/sessions/correction_force/realtime-corrections/run-once",
            json={"force": True},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["called"] is True
        assert body["gate"]["reason"] == "forced"
        assert body["revision_count"] == 1
        assert len(body["transcript_revisions"]) == 1
        assert len(calls) == 1
        assert "sk-test" not in response.text
        assert "gw.example" not in response.text

        fetched = client.get("/live/asr/sessions/correction_force/events").json()
        revisions = [event for event in fetched["events"] if event.get("event_type") == "transcript_revision"]
        assert len(revisions) == 1
        assert revisions[0]["payload"]["text"] == "接口先灰度 5%，如果错误率超过 0.1% 就回滚。"
        correction_status = fetched["realtime_transcript_correction"]
        assert correction_status["status"] == "completed"
        assert correction_status["reservation"]["status"] == "completed"
        assert len(correction_status["batch_audits"]) == 1
        audit = correction_status["batch_audits"][0]
        assert audit["usage"]["total_tokens"] == 30
        assert audit["provider"] == "team_gateway"
        assert audit["model"] == "gpt-5.5"
        assert audit["purpose"] == "realtime_transcript_correction"
        assert audit["degraded"] is False
        assert audit["fallback"] is True
        assert audit["retry"] is False
        assert audit["status"] == "completed"
        assert revisions[0]["payload"]["correction"] == {
            "policy_version": "realtime-transcript-correction.v1",
            "source": "fallback_batch",
            "batch_id": audit["batch_id"],
        }
        transcript_export = client.get("/live/asr/sessions/correction_force/transcript.txt")
        assert transcript_export.status_code == 200
        assert "接口先灰度 5%，如果错误率超过 0.1% 就回滚。" in transcript_export.text
        assert "百分之零点一" not in transcript_export.text
    finally:
        ws.send_text("END")
        context.__exit__(None, None, None)


def test_multi_segment_fallback_records_usage_once_in_batch_audit(monkeypatch, tmp_path):
    _configure_llm(monkeypatch)
    monkeypatch.setattr(
        asr_stream,
        "get_recognizer",
        lambda sid: _TwoSegmentCorrectionRecognizer(sid, "unused"),
    )

    def correct(raw, cfg, **kwargs):
        return (
            raw.replace("灰度百分之五", "灰度 5%")
            .replace("百分之零点一", "0.1%")
            .replace("超过0.1%就", "超过 0.1% 就"),
            {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
            False,
        )

    monkeypatch.setattr(asr_correct, "correct_transcript", correct)
    client = TestClient(create_app(data_dir=tmp_path))
    _enable_l2(client)
    websocket = client.websocket_connect("/live/asr/stream/ws/correction_multi_batch?audio_source=browser_live_mic")
    ws = websocket.__enter__()
    ws.send_bytes(b"\x00" * 3200)
    assert len(_receive_final_events(ws, 2)) == 2
    try:
        response = client.post(
            "/live/asr/sessions/correction_multi_batch/realtime-corrections/run-once",
            json={"force": True},
        )

        assert response.status_code == 200
        assert response.json()["revision_count"] == 2
        fetched = client.get("/live/asr/sessions/correction_multi_batch/events").json()
        revisions = [event for event in fetched["events"] if event.get("event_type") == "transcript_revision"]
        audits = fetched["realtime_transcript_correction"]["batch_audits"]
        assert len(revisions) == 2
        assert len(audits) == 1
        assert audits[0]["segment_ids"] == ["corr_seg_1", "corr_seg_2"]
        assert audits[0]["usage"] == {
            "prompt_tokens": 20,
            "completion_tokens": 10,
            "total_tokens": 30,
        }
        assert {revision["payload"]["correction"]["batch_id"] for revision in revisions} == {audits[0]["batch_id"]}
        assert all("usage" not in revision["payload"]["correction"] for revision in revisions)
    finally:
        ws.send_text("END")
        websocket.__exit__(None, None, None)


def test_partial_correction_keeps_accepted_revision_and_reports_rejected_segment(
    monkeypatch,
    tmp_path,
):
    _configure_llm(monkeypatch)

    def correct(raw, cfg, **kwargs):
        del cfg
        first_marker = "<<<MC_SEGMENT:0001:corr_seg_1>>>"
        second_marker = "<<<MC_SEGMENT:0002:corr_seg_2>>>"
        first = raw.split(first_marker, 1)[1].split("<<<MC_END:0001>>>", 1)[0].strip()
        return (
            "\n".join(
                [
                    first_marker,
                    first.replace("恢度", "灰度").replace("百分之五", "5%"),
                    "<<<MC_END:0001>>>",
                    second_marker,
                    "完全不同的事实。",
                    "<<<MC_END:0002>>>",
                ]
            ),
            {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
            False,
        )

    monkeypatch.setattr(asr_correct, "correct_transcript", correct)
    monkeypatch.setattr(
        asr_stream,
        "get_recognizer",
        lambda sid: _TwoSegmentCorrectionRecognizer(sid, "unused"),
    )
    client = TestClient(create_app(data_dir=tmp_path))
    _enable_l2(client)
    websocket = client.websocket_connect("/live/asr/stream/ws/correction_partial_batch?audio_source=browser_live_mic")
    ws = websocket.__enter__()
    ws.send_bytes(b"\x00" * 3200)
    assert len(_receive_final_events(ws, 2)) == 2
    try:
        response = client.post(
            "/live/asr/sessions/correction_partial_batch/realtime-corrections/run-once",
            json={"force": True},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["revision_count"] == 1
        assert body["status"]["status"] == "partially_completed"
        assert body["status"]["revised_segment_ids"] == ["corr_seg_1"]
        assert body["status"]["rejected_segment_ids"] == ["corr_seg_2"]
        fetched = client.get("/live/asr/sessions/correction_partial_batch/events").json()
        audit = fetched["realtime_transcript_correction"]["batch_audits"][0]
        assert audit["status"] == "partially_completed"
    finally:
        ws.send_text("END")
        websocket.__exit__(None, None, None)


def test_provider_failure_commits_reservation_and_single_batch_audit(monkeypatch, tmp_path):
    _configure_llm(monkeypatch)

    def fail(raw, cfg, **kwargs):
        persisted = SqliteAsrLiveSessionRepository(tmp_path).get("correction_provider_failure")
        assert persisted["realtime_transcript_correction"]["reservation"]["status"] == "reserved"
        raise RuntimeError("provider unavailable at https://private.example/v1?api_key=sk-secret")

    monkeypatch.setattr(asr_correct, "correct_transcript", fail)
    client = TestClient(create_app(data_dir=tmp_path), raise_server_exceptions=False)
    _enable_l2(client)
    context, ws = _start_live_session(
        monkeypatch,
        client,
        "correction_provider_failure",
        "接口先灰度百分之五。",
    )
    try:
        response = client.post(
            "/live/asr/sessions/correction_provider_failure/realtime-corrections/run-once",
            json={"force": True},
        )

        assert response.status_code == 502
        assert response.json()["detail"] == {
            "error_code": "realtime_correction_provider_failed",
            "message": "Realtime correction provider request failed",
        }
        assert "private.example" not in response.text
        assert "sk-secret" not in response.text
        fetched = client.get("/live/asr/sessions/correction_provider_failure/events").json()
        status = fetched["realtime_transcript_correction"]
        assert status["reservation"]["status"] == "provider_failed"
        assert len(status["batch_audits"]) == 1
        assert status["batch_audits"][0]["status"] == "provider_failed"
        assert status["batch_audits"][0]["usage"] == {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        assert status["batch_audits"][0]["error_code"] == "realtime_correction_provider_failed"
        assert status["batch_audits"][0]["provider"] == "team_gateway"
        assert status["batch_audits"][0]["model"] == "gpt-5.5"
        assert status["batch_audits"][0]["purpose"] == "realtime_transcript_correction"
        assert status["batch_audits"][0]["degraded"] is True
        assert status["batch_audits"][0]["fallback"] is True
        assert status["batch_audits"][0]["retry"] is False
    finally:
        ws.send_text("END")
        context.__exit__(None, None, None)


def test_real_correction_client_failure_uses_502_audit_instead_of_rejected_original(
    monkeypatch,
    tmp_path,
):
    _configure_llm(monkeypatch)
    monkeypatch.setattr(llm_service.time, "sleep", lambda *a: None)
    calls = []

    class FailingClient:
        def post_json(self, url, headers, body, timeout):
            calls.append(body)
            raise llm_service.LlmProviderHttpError(401, provider_code="INVALID_API_KEY")

    monkeypatch.setattr(asr_correct, "HttpxLlmClient", FailingClient)
    client = TestClient(create_app(data_dir=tmp_path), raise_server_exceptions=False)
    _enable_l2(client)
    context, ws = _start_live_session(
        monkeypatch,
        client,
        "correction_real_client_failure",
        "接口先灰度百分之五。",
    )
    try:
        response = client.post(
            "/live/asr/sessions/correction_real_client_failure/realtime-corrections/run-once",
            json={"force": True},
        )

        assert response.status_code == 502
        assert response.json()["detail"] == {
            "error_code": "realtime_correction_provider_failed",
            "message": "Realtime correction provider request failed",
        }
        assert "private.example" not in response.text
        assert "sk-secret" not in response.text
        assert len(calls) == 1
        fetched = client.get("/live/asr/sessions/correction_real_client_failure/events").json()
        status = fetched["realtime_transcript_correction"]
        assert status["status"] == "provider_failed_terminal"
        assert status["terminal_failed_segment_ids"] == ["corr_seg_1"]
        assert status["reservation"]["status"] == "provider_failed"
        assert status.get("rejected_segment_ids", []) == []
        assert not any(event.get("event_type") == "transcript_revision" for event in fetched["events"])
        assert [audit["status"] for audit in status["batch_audits"]] == ["provider_failed"]
    finally:
        ws.send_text("END")
        context.__exit__(None, None, None)


def test_durable_correction_second_attempt_retries_once_failed_provider_segment(
    monkeypatch,
    tmp_path,
):
    _configure_llm(monkeypatch)
    monkeypatch.setattr(llm_service.time, "sleep", lambda *a: None)
    calls = []

    class FailingClient:
        def post_json(self, url, headers, body, timeout):
            calls.append(body)
            raise llm_service.LlmProviderTransportError("transport")

    monkeypatch.setattr(asr_correct, "HttpxLlmClient", FailingClient)
    app = create_app(data_dir=tmp_path)
    client = TestClient(app, raise_server_exceptions=False)
    _enable_l2(client)
    long_final = (
        "接口先灰度百分之五，如果错误率超过百分之零点一就回滚，"
        "负责人今天补齐监控看板和告警阈值，并确认发布窗口与回滚脚本。"
        "上线前还要完成容量检查、数据库备份演练和依赖服务健康检查。"
    )
    assert len(long_final) >= 80
    context, ws = _start_live_session(
        monkeypatch,
        client,
        "durable_correction_provider_retry",
        long_final,
    )
    try:
        persistence = app.state.v2_persistence
        job = persistence.list_jobs(
            meeting_id="durable_correction_provider_retry",
            lane="correction",
        )[0]

        with pytest.raises(HTTPException) as first_failure:
            app.state.v2_correction_job_handler_impl({**job, "attempts": 1})
        with pytest.raises(HTTPException) as second_failure:
            app.state.v2_correction_job_handler_impl({**job, "attempts": 2})

        assert first_failure.value.status_code == 502
        assert second_failure.value.status_code == 502
        assert getattr(first_failure.value, "retryable", True) is True
        assert getattr(second_failure.value, "retryable", True) is False
        assert len(calls) == 2
        fetched = client.get("/live/asr/sessions/durable_correction_provider_retry/events").json()
        status = fetched["realtime_transcript_correction"]
        assert status["failure_counts"] == {"corr_seg_1": 2}
        assert status["terminal_failed_segment_ids"] == ["corr_seg_1"]
        assert status.get("rejected_segment_ids", []) == []
        assert [audit["retry"] for audit in status["batch_audits"]] == [False, True]
    finally:
        ws.send_text("END")
        context.__exit__(None, None, None)


def test_provider_failure_consumes_segment_budget_after_one_retry(monkeypatch, tmp_path):
    _configure_llm(monkeypatch)
    calls = []

    def fail(raw, cfg, **kwargs):
        calls.append(raw)
        raise llm_service.LlmProviderTransportError("transport")

    monkeypatch.setattr(asr_correct, "correct_transcript", fail)
    client = TestClient(create_app(data_dir=tmp_path), raise_server_exceptions=False)
    _enable_l2(client)
    context, ws = _start_live_session(
        monkeypatch,
        client,
        "correction_provider_retry_budget",
        "接口先灰度百分之五。",
    )
    try:
        first = client.post(
            "/live/asr/sessions/correction_provider_retry_budget/realtime-corrections/run-once",
            json={"force": True},
        )
        second = client.post(
            "/live/asr/sessions/correction_provider_retry_budget/realtime-corrections/run-once",
            json={"force": True},
        )
        third = client.post(
            "/live/asr/sessions/correction_provider_retry_budget/realtime-corrections/run-once",
            json={"force": True},
        )

        assert first.status_code == 502
        assert second.status_code == 502
        assert third.status_code == 200
        assert third.json()["called"] is False
        assert third.json()["gate"]["reason"] == "no_unrevised_final"
        assert len(calls) == 2
        status = client.get("/live/asr/sessions/correction_provider_retry_budget/events").json()[
            "realtime_transcript_correction"
        ]
        assert status["failure_counts"] == {"corr_seg_1": 2}
        assert status["failed_segment_ids"] == ["corr_seg_1"]
        assert status["terminal_failed_segment_ids"] == ["corr_seg_1"]
        assert status["processed_segment_ids"] == ["corr_seg_1"]
        assert [audit["retry"] for audit in status["batch_audits"]] == [False, True]
    finally:
        ws.send_text("END")
        context.__exit__(None, None, None)


def test_two_app_instances_atomically_claim_one_realtime_correction_batch(monkeypatch, tmp_path):
    _configure_llm(monkeypatch)
    provider_entered = Event()
    release_provider = Event()
    calls = []

    def correct(raw, cfg, **kwargs):
        calls.append(raw)
        provider_entered.set()
        assert release_provider.wait(timeout=3)
        return raw.replace("灰度百分之五", "灰度 5%"), {"total_tokens": 1}, False

    monkeypatch.setattr(asr_correct, "correct_transcript", correct)
    first_client = TestClient(create_app(data_dir=tmp_path), raise_server_exceptions=False)
    second_client = TestClient(create_app(data_dir=tmp_path), raise_server_exceptions=False)
    _enable_l2(first_client)
    context, ws = _start_live_session(
        monkeypatch,
        first_client,
        "correction_atomic_claim",
        "接口先灰度百分之五。",
    )
    original_get = SqliteAsrLiveSessionRepository.get
    stale_read_barrier = Barrier(2)
    synchronized_reads = {"count": 0}

    def synchronized_get(repo, session_id):
        record = original_get(repo, session_id)
        if session_id == "correction_atomic_claim" and synchronized_reads["count"] < 2:
            synchronized_reads["count"] += 1
            stale_read_barrier.wait(timeout=3)
        return record

    monkeypatch.setattr(SqliteAsrLiveSessionRepository, "get", synchronized_get)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first_future = pool.submit(
                first_client.post,
                "/live/asr/sessions/correction_atomic_claim/realtime-corrections/run-once",
                json={"force": True},
            )
            second_future = pool.submit(
                second_client.post,
                "/live/asr/sessions/correction_atomic_claim/realtime-corrections/run-once",
                json={"force": True},
            )
            assert provider_entered.wait(timeout=2)
            time.sleep(0.1)
            calls_before_release = list(calls)
            release_provider.set()
            responses = [first_future.result(timeout=5), second_future.result(timeout=5)]

        assert len(calls_before_release) == 1
        assert len(calls) == 1
        assert sorted(response.json()["called"] for response in responses) == [False, True]
    finally:
        ws.send_text("END")
        context.__exit__(None, None, None)


def test_realtime_correction_rejects_batch_when_markers_are_not_preserved(monkeypatch, tmp_path):
    _configure_llm(monkeypatch)
    monkeypatch.setattr(
        asr_correct,
        "correct_transcript",
        lambda raw, cfg, **kwargs: ("没有任何分隔符的输出", {"total_tokens": 3}, False),
    )
    client = TestClient(create_app(data_dir=tmp_path))
    _enable_l2(client)
    context, ws = _start_live_session(
        monkeypatch,
        client,
        "correction_mapping_reject",
        "接口先恢度百分之五，如果错误率超过百分之零点一就回滚。",
    )
    try:
        response = client.post(
            "/live/asr/sessions/correction_mapping_reject/realtime-corrections/run-once",
            json={"force": True},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["called"] is True
        assert body["revision_count"] == 0
        assert body["status"]["status"] == "mapping_rejected"
        fetched = client.get("/live/asr/sessions/correction_mapping_reject/events").json()
        assert not any(event.get("event_type") == "transcript_revision" for event in fetched["events"])
    finally:
        ws.send_text("END")
        context.__exit__(None, None, None)


def test_rejected_correction_is_not_processed_and_gets_one_forced_retry(monkeypatch, tmp_path):
    _configure_llm(monkeypatch)
    calls = []

    def unchanged(raw, cfg, **kwargs):
        calls.append(raw)
        return raw.replace("接口先灰度五趴。", "完全不同的事实。"), {"total_tokens": 2}, False

    monkeypatch.setattr(asr_correct, "correct_transcript", unchanged)
    client = TestClient(create_app(data_dir=tmp_path))
    _enable_l2(client)
    context, ws = _start_live_session(monkeypatch, client, "correction_retry", "接口先灰度五趴。")
    try:
        first = client.post(
            "/live/asr/sessions/correction_retry/realtime-corrections/run-once",
            json={"force": True},
        ).json()
        second = client.post(
            "/live/asr/sessions/correction_retry/realtime-corrections/run-once",
            json={"force": True},
        ).json()
        third = client.post(
            "/live/asr/sessions/correction_retry/realtime-corrections/run-once",
            json={"force": True},
        ).json()

        assert first["status"]["status"] == "correction_rejected"
        assert "corr_seg_1" not in first["status"].get("processed_segment_ids", [])
        assert second["called"] is True
        assert third["called"] is False
        assert third["gate"]["reason"] == "no_unrevised_final"
        assert len(calls) == 2
    finally:
        ws.send_text("END")
        context.__exit__(None, None, None)


def test_unchanged_success_is_processed_without_paid_stop_retry(monkeypatch, tmp_path):
    _configure_llm(monkeypatch)
    calls = []

    def unchanged_success(raw, cfg, **kwargs):
        calls.append(raw)
        return raw, {"total_tokens": 2}, False

    monkeypatch.setattr(asr_correct, "correct_transcript", unchanged_success)
    client = TestClient(create_app(data_dir=tmp_path))
    _enable_l2(client)
    context, ws = _start_live_session(monkeypatch, client, "correction_no_change", "接口先灰度五趴。")
    try:
        first = client.post(
            "/live/asr/sessions/correction_no_change/realtime-corrections/run-once",
            json={"force": True},
        ).json()
        second = client.post(
            "/live/asr/sessions/correction_no_change/realtime-corrections/run-once",
            json={"force": True},
        ).json()

        assert first["status"]["status"] == "no_revision_needed"
        assert first["no_revision_segment_ids"] == ["corr_seg_1"]
        assert "corr_seg_1" in first["status"]["processed_segment_ids"]
        assert second["called"] is False
        assert second["gate"]["reason"] == "no_unrevised_final"
        assert len(calls) == 1
    finally:
        ws.send_text("END")
        context.__exit__(None, None, None)


def test_llm_session_lock_is_released_and_removed_after_request(monkeypatch, tmp_path):
    _configure_llm(monkeypatch)
    app = create_app(data_dir=tmp_path)
    client = TestClient(app)
    response = client.post(
        "/live/asr/sessions/missing/realtime-corrections/run-once",
        json={"force": False},
    )

    assert response.status_code == 404
    assert app.state.llm_session_locks == {}


def test_realtime_correction_request_rejects_unknown_fields(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    response = client.post(
        "/live/asr/sessions/missing/realtime-corrections/run-once",
        json={"force": False, "api_key": "must-not-be-accepted"},
    )

    assert response.status_code == 422


def test_semantic_quality_blocker_is_recovered_before_formal_suggestion(
    monkeypatch,
    tmp_path,
):
    _configure_llm(monkeypatch)
    raw_text = (
        "这次check acout out service周五晚上灰度百分之十先看ror rate和p九九b"
        "如果指标异常我们暂停扩量但回滚脚本还没有在凌晨older ker消消堆堆积"
        "lag最高到了八万告警延迟了六分钟临时扩容已经止血根因可能是库存。"
        "reqzzxqv noizzz qqqq"
    )
    corrected_text = (
        "这次 checkout 服务周五晚上灰度百分之十，先看 error rate 和 P99，"
        "如果指标异常我们暂停扩量，但回滚脚本还没有完善。在凌晨，worker 消费堆积，"
        "lag 最高到了八万，告警延迟了六分钟。临时扩容已经止血，根因可能是库存。"
    )

    def correct_batch(raw, cfg, **kwargs):
        lines = raw.splitlines()
        return raw.replace(lines[1], corrected_text, 1), {"total_tokens": 42}, False

    monkeypatch.setattr(asr_correct, "correct_transcript", correct_batch)

    class FakeSuggestionClient:
        def post_json(self, url, headers, body, timeout):
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "suggestion_text": "建议确认灰度期间的回滚负责人和告警阈值。",
                                    "confidence": 0.92,
                                    "trigger_reason": "灰度发布缺少回滚责任和监控确认",
                                },
                                ensure_ascii=False,
                            ),
                        },
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18},
            }

    monkeypatch.setattr(llm_service, "HttpxLlmClient", lambda: FakeSuggestionClient())
    client = TestClient(create_app(data_dir=tmp_path))
    _enable_l2(client)
    context, ws = _start_live_session(monkeypatch, client, "semantic_quality_recovery", raw_text)
    try:
        before = client.get("/live/asr/sessions/semantic_quality_recovery/events").json()
        assert before["event_source"]["asr_semantic_quality"]["status"] == "blocked"
        assert "asr_semantic_quality_blocked" in before["event_source"]["acceptance_blockers"]

        correction = client.post(
            "/live/asr/sessions/semantic_quality_recovery/realtime-corrections/run-once",
            json={"force": False},
        )
        assert correction.status_code == 200
        correction_body = correction.json()
        assert correction_body["called"] is True, json.dumps(correction_body, ensure_ascii=False)
        assert correction_body["gate"]["reason"] in {
            "semantic_quality_recovery",
            "min_batch_chars_reached",
        }
        assert correction_body["revision_count"] == 1

        # A later ASR persistence checkpoint must project the accepted revision,
        # rather than reintroducing the raw semantic-quality blocker.
        ws.send_bytes(b"\x00" * 3200)
        assert _receive_final_events(ws, 1)[0]["event_type"] == "final"
        recovered = client.get("/live/asr/sessions/semantic_quality_recovery/events").json()
        assert recovered["event_source"]["asr_semantic_quality"]["status"] == "passed"
        assert "asr_semantic_quality_blocked" not in recovered["event_source"]["acceptance_blockers"]

        suggestions = client.post(
            "/live/asr/sessions/semantic_quality_recovery/auto-suggestions/run-once",
        )
        assert suggestions.status_code == 200
        assert suggestions.json()["generated_card_count"] == 1
        assert suggestions.json()["suggestion_cards"]
    finally:
        ws.send_text("END")
        context.__exit__(None, None, None)
