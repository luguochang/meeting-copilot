from concurrent.futures import ThreadPoolExecutor
import json
from threading import Event
import time

from fastapi.testclient import TestClient

from meeting_copilot_web_mvp import (
    asr_stream,
    auto_suggestion_orchestrator,
    llm_service,
    realtime_transcript_correction,
)
from meeting_copilot_web_mvp.app import create_app


class _AutoSuggestionRecognizer:
    provider = "test_contract_realtime_asr"
    provider_mode = "real"
    is_mock = False
    fallback_used = False
    degradation_reasons = []

    def __init__(self, session_id, *, confidence=0.9):
        self.session_id = session_id
        self._seq = 0
        self._confidence = confidence

    def recognize_chunk(self, pcm):
        self._seq += 1
        return [{
            "event_type": "partial",
            "segment_id": "auto_seg",
            "text": "接口先灰度",
            "start_ms": 0,
            "end_ms": 300,
            "confidence": self._confidence,
        }]

    def finalize(self):
        return [{
            "event_type": "final",
            "segment_id": "auto_seg",
            "text": "接口先灰度 5%，如果错误率超过 0.1% 就回滚。",
            "start_ms": 0,
            "end_ms": 900,
            "confidence": self._confidence,
        }]


class _LiveFinalRecognizer:
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
            "event_type": "final",
            "segment_id": "live_seg",
            "text": "接口先灰度 5%，如果错误率超过 0.1% 就回滚，owner 张三今天补 SLO 看板。",
            "start_ms": 0,
            "end_ms": 900,
            "confidence": 0.9,
        }]

    def finalize(self):
        return [{
            "event_type": "final",
            "segment_id": "live_seg_end",
            "text": "接口先灰度 5%，如果错误率超过 0.1% 就回滚，owner 张三今天补 SLO 看板。",
            "start_ms": 0,
            "end_ms": 900,
            "confidence": 0.9,
        }]


class _MixedLanguageNoiseRecognizer:
    provider = "test_contract_realtime_asr"
    provider_mode = "real"
    is_mock = False
    fallback_used = False
    degradation_reasons = []

    def __init__(self, session_id):
        self.session_id = session_id

    def recognize_chunk(self, pcm):
        return [{
            "event_type": "partial",
            "segment_id": "mixed_noise_seg",
            "text": "接口先灰度",
            "start_ms": 0,
            "end_ms": 300,
            "confidence": 0.9,
        }]

    def finalize(self):
        return [{
            "event_type": "final",
            "segment_id": "mixed_noise_seg",
            "text": (
                "接口先灰度 5%，如果错误率超过 0.1% 就回滚，owner 张三今天补 SLO 看板。"
                "request到contest xt moden downtwo calling to methoc ine ofdel背熟midiwell"
                "le的六值和位置背书三三状态一个短期机一个常见机一外一个任务状态"
            ),
            "start_ms": 0,
            "end_ms": 3_000,
            "confidence": 0.9,
        }]


def _create_realtime_session(monkeypatch, client, session_id: str, *, confidence: float = 0.9):
    monkeypatch.setattr(
        asr_stream,
        "get_recognizer",
        lambda sid: _AutoSuggestionRecognizer(sid, confidence=confidence),
    )
    monkeypatch.setattr(asr_stream, "_correct_transcript", lambda raw, cfg: (raw, {"total_tokens": 0}, False))
    with client.websocket_connect(f"/live/asr/stream/ws/{session_id}?audio_source=browser_live_mic") as ws:
        ws.send_bytes(b"\x00" * 3200)
        ws.receive_text()
        ws.send_text("END")
        while True:
            event = json.loads(ws.receive_text())
            if event.get("event_type") == "final":
                break
    response = client.get(f"/live/asr/sessions/{session_id}/events")
    assert response.status_code == 200
    return response.json()


def _receive_until_event(ws, event_type: str):
    while True:
        event = json.loads(ws.receive_text())
        if event.get("event_type") == event_type:
            return event


def _fake_llm(monkeypatch, calls):
    class FakeClient:
        def post_json(self, url, headers, body, timeout):
            calls.append(json.loads(json.dumps(body, ensure_ascii=False)))
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "suggestion_text": "建议确认回滚 owner 和监控口径。",
                                    "confidence": 0.86,
                                    "trigger_reason": "灰度发布缺少 owner",
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18},
            }

    monkeypatch.setattr(llm_service, "HttpxLlmClient", lambda: FakeClient())
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gw.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "gpt-5.5")
    monkeypatch.delenv("LLM_GATEWAY_IS_MOCK", raising=False)


def _auto_record_with_candidates(*, at_ms_values=(1_000, 2_000), status=None):
    events = []
    previews = []
    for index, at_ms in enumerate(at_ms_values, start=1):
        candidate_id = f"candidate_{index}"
        events.append({
            "event_type": "suggestion_candidate_event",
            "at_ms": at_ms,
            "payload": {
                "candidate_id": candidate_id,
                "scheduler_event_type": "llm_candidate_queued",
                "confidence": 0.9,
                "degradation_reasons": [],
            },
        })
        previews.append({
            "execution_id": f"exec_{index}",
            "target_candidate_id": candidate_id,
            "candidate_confidence": 0.9,
            "candidate_degradation_reasons": [],
        })
    record = {
        "session_id": "auto_unit",
        "events": events,
        "suggestion_cards": [],
    }
    if status is not None:
        record["auto_suggestion"] = status
    return record, previews


def _execution_candidate_ids(record):
    return [
        str((event.get("payload") or {}).get("candidate_id") or "")
        for event in record.get("events") or []
        if event.get("event_type") == "suggestion_candidate_event"
        and (event.get("payload") or {}).get("scheduler_event_type")
        == "llm_candidate_queued"
    ]


def _fake_candidate_executor(monkeypatch, calls):
    def execute_candidate(preview, config):
        calls.append(preview["target_candidate_id"])
        return {
            **preview,
            "run_status": "completed",
            "card": {
                "card_id": f"card_{preview['target_candidate_id']}",
                "suggestion_text": "建议确认 owner 和回滚条件。",
                "evidence_span_ids": ["asr_ev_1"],
            },
            "llm_usage": {"total_tokens": 1},
        }

    monkeypatch.setattr(auto_suggestion_orchestrator.llm_service, "execute_candidate", execute_candidate)


def _apply_runtime_policy(
    record,
    *,
    enabled=True,
    confidence_threshold=0.7,
    window_seconds=20,
    cooldown_minutes=5,
    degradation_level=0,
):
    policy = auto_suggestion_orchestrator.build_runtime_policy(
        {
            "enabled": enabled,
            "confidence_threshold": confidence_threshold,
            "window_seconds": window_seconds,
            "cooldown_minutes": cooldown_minutes,
        },
        degradation_level=degradation_level,
    )
    updated, _status = auto_suggestion_orchestrator.apply_runtime_policy(record, policy)
    return updated


def _attach_final_evidence(record, preview, *, segment_id="seg_1"):
    text = "接口先恢度百分之五，如果 P 九九延迟超过九百毫秒就回滚。"
    evidence_span_id = f"asr_ev_{segment_id}"
    record["events"].insert(0, {
        "id": f"transcript_final:{segment_id}",
        "event_type": "transcript_final",
        "at_ms": 30_000,
        "payload": {
            "segment_id": segment_id,
            "text": text,
            "normalized_text": text,
            "start_ms": 0,
            "end_ms": 30_000,
            "confidence": 0.9,
            "evidence_spans": [{
                "id": evidence_span_id,
                "segment_id": segment_id,
                "quote": text,
                "start_ms": 0,
                "end_ms": 30_000,
                "status": "active",
            }],
        },
    })
    preview.update({
        "segment_batch": [segment_id],
        "evidence_span_ids": [evidence_span_id],
        "evidence_spans": [{
            "id": evidence_span_id,
            "segment_id": segment_id,
            "quote": text,
            "start_ms": 0,
            "end_ms": 30_000,
            "status": "active",
        }],
    })
    return text, evidence_span_id


def test_stable_partial_candidate_never_calls_remote_llm(monkeypatch):
    record, previews = _auto_record_with_candidates(at_ms_values=(31_000,))
    record["events"][0]["payload"]["degradation_reasons"] = ["partial_not_final"]
    previews[0]["candidate_degradation_reasons"] = ["partial_not_final"]
    calls = []
    _fake_candidate_executor(monkeypatch, calls)

    updated, status, runs = auto_suggestion_orchestrator.run_once(
        record,
        previews=previews,
        config=llm_service.LlmConfig(base_url="https://gw.example", api_key="sk-test", model="gpt-5.5"),
        acceptance_blockers=[],
    )

    assert calls == []
    assert runs == []
    assert updated["suggestion_cards"] == []
    assert status["suppressed"][-1]["reason"] == "partial_not_final"


def test_provider_failure_consumes_attempt_budget_and_does_not_retry_same_candidate(monkeypatch):
    record, previews = _auto_record_with_candidates(at_ms_values=(31_000,))
    calls = []

    def fail(preview, config):
        calls.append(preview["target_candidate_id"])
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(auto_suggestion_orchestrator.llm_service, "execute_candidate", fail)
    config = llm_service.LlmConfig(base_url="https://gw.example", api_key="sk-test", model="gpt-5.5")

    first, first_status, first_runs = auto_suggestion_orchestrator.run_once(
        record,
        previews=previews,
        config=config,
        acceptance_blockers=[],
    )
    second, second_status, second_runs = auto_suggestion_orchestrator.run_once(
        first,
        previews=previews,
        config=config,
        acceptance_blockers=[],
    )
    third, third_status, third_runs = auto_suggestion_orchestrator.run_once(
        second,
        previews=previews,
        config=config,
        acceptance_blockers=[],
    )

    assert calls == ["candidate_1", "candidate_1"]
    assert first_runs[0]["run_status"] == "provider_failed"
    assert first_status["processed_candidate_ids"] == []
    assert first_status["candidate_reservations"]["candidate_1"]["status"] == "retry_pending"
    assert first_status["call_count_last_hour"] == 1
    assert first_status["suppressed"][-1]["reason"] == "provider_error"
    assert first_runs[0]["provider_error_type"] == "RuntimeError"
    assert first_runs[0]["call_finished_at_ms"] >= first_runs[0]["call_started_at_ms"]
    assert first_status["suppressed"][-1]["at_ms"] == first_runs[0]["call_finished_at_ms"]
    assert second_runs[0]["run_status"] == "provider_failed"
    assert second_status["processed_candidate_ids"] == ["candidate_1"]
    assert second_status["candidate_reservations"]["candidate_1"]["status"] == "terminal_failed"
    assert third_runs == []
    assert third_status["processed_candidate_ids"] == ["candidate_1"]
    assert third["suggestion_cards"] == []


def test_two_app_instances_atomically_claim_one_auto_suggestion_candidate(monkeypatch, tmp_path):
    config = llm_service.LlmConfig(
        base_url="https://gw.example",
        api_key="sk-test",
        model="gpt-5.5",
        provider_label="team_gateway",
    )
    monkeypatch.setattr(llm_service.LlmConfig, "from_env", classmethod(lambda cls: config))
    provider_entered = Event()
    release_provider = Event()
    calls = []

    def execute_candidate(preview, provider_config):
        calls.append(preview["target_candidate_id"])
        provider_entered.set()
        assert release_provider.wait(timeout=3)
        return {
            **preview,
            "run_status": "completed",
            "card": {"card_id": "atomic_auto_card", "suggestion_text": "建议确认回滚 owner。"},
            "llm_usage": {"total_tokens": 1},
        }

    monkeypatch.setattr(auto_suggestion_orchestrator.llm_service, "execute_candidate", execute_candidate)
    first_client = TestClient(create_app(data_dir=tmp_path))
    second_client = TestClient(create_app(data_dir=tmp_path))
    _create_realtime_session(monkeypatch, first_client, "auto_atomic_claim")

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(
            first_client.post,
            "/live/asr/sessions/auto_atomic_claim/auto-suggestions/run-once",
        )
        assert provider_entered.wait(timeout=2)
        second_future = pool.submit(
            second_client.post,
            "/live/asr/sessions/auto_atomic_claim/auto-suggestions/run-once",
        )
        time.sleep(0.1)
        calls_before_release = list(calls)
        release_provider.set()
        responses = [first_future.result(timeout=5), second_future.result(timeout=5)]

    assert len(calls_before_release) == 1
    assert len(calls) == 1
    assert sorted(response.json()["generated_card_count"] for response in responses) == [0, 1]
    persisted = first_client.get("/live/asr/sessions/auto_atomic_claim/events").json()
    assert len(persisted["suggestion_cards"]) == 1


def test_concurrent_pause_is_preserved_when_auto_suggestion_provider_finishes(monkeypatch, tmp_path):
    config = llm_service.LlmConfig(
        base_url="https://gw.example",
        api_key="sk-test",
        model="gpt-5.5",
        provider_label="team_gateway",
    )
    monkeypatch.setattr(llm_service.LlmConfig, "from_env", classmethod(lambda cls: config))
    provider_entered = Event()
    release_provider = Event()

    def execute_candidate(preview, provider_config):
        provider_entered.set()
        assert release_provider.wait(timeout=3)
        return {
            **preview,
            "run_status": "completed",
            "card": {"card_id": "pause_race_card", "suggestion_text": "建议确认回滚 owner。"},
            "llm_usage": {"total_tokens": 1},
        }

    monkeypatch.setattr(auto_suggestion_orchestrator.llm_service, "execute_candidate", execute_candidate)
    first_client = TestClient(create_app(data_dir=tmp_path))
    second_client = TestClient(create_app(data_dir=tmp_path))
    _create_realtime_session(monkeypatch, first_client, "auto_pause_race")

    with ThreadPoolExecutor(max_workers=1) as pool:
        run_future = pool.submit(
            first_client.post,
            "/live/asr/sessions/auto_pause_race/auto-suggestions/run-once",
        )
        assert provider_entered.wait(timeout=2)
        pause = second_client.patch(
            "/live/asr/sessions/auto_pause_race/auto-suggestions/status",
            json={"paused": True},
        )
        release_provider.set()
        run = run_future.result(timeout=5)

    persisted = first_client.get("/live/asr/sessions/auto_pause_race/events").json()
    assert pause.status_code == 200
    assert run.status_code == 200
    assert run.json()["status"]["paused"] is True
    assert run.json()["status"]["status"] == "paused"
    assert persisted["auto_suggestion"]["paused"] is True
    assert persisted["auto_suggestion"]["status"] == "paused"


def test_status_patch_atomically_preserves_interleaved_reservation_and_provider_merge(
    monkeypatch,
    tmp_path,
):
    config = llm_service.LlmConfig(
        base_url="https://gw.example",
        api_key="sk-test",
        model="m1",
        provider_label="team_gateway",
    )
    monkeypatch.setattr(
        llm_service.LlmConfig,
        "from_env",
        classmethod(lambda cls: config),
    )
    calls = []

    def execute_candidate(preview, provider_config):
        calls.append(preview["target_candidate_id"])
        return {
            **preview,
            "run_status": "completed",
            "card": {
                "card_id": "atomic_patch_card",
                "suggestion_text": "建议确认回滚 owner。",
                "confidence": 0.95,
            },
            "llm_usage": {"total_tokens": 1},
        }

    monkeypatch.setattr(
        auto_suggestion_orchestrator.llm_service,
        "execute_candidate",
        execute_candidate,
    )
    first_app = create_app(data_dir=tmp_path)
    second_app = create_app(data_dir=tmp_path)
    first_client = TestClient(first_app)
    second_client = TestClient(second_app)
    session_id = "auto_atomic_patch"
    _create_realtime_session(monkeypatch, first_client, session_id, confidence=0.95)
    first_repo = first_app.state.asr_live_repository
    second_repo = second_app.state.asr_live_repository
    original_replace = first_repo.replace
    original_update = first_repo.update
    injected = {"done": False}

    def inject_reservation_once():
        if injected["done"]:
            return
        injected["done"] = True

        def reserve(latest):
            status = auto_suggestion_orchestrator.status_from_record(latest)
            candidate_id = str(
                _execution_candidate_ids(latest)[0]
            )
            status["candidate_attempt_counts"] = {candidate_id: 1}
            status["candidate_reservations"] = {
                candidate_id: {
                    "candidate_id": candidate_id,
                    "claim_id": "cross_app_claim",
                    "status": "retry_pending",
                    "reserved_at_ms": 10_000,
                    "lease_expires_at_ms": 70_000,
                    "attempt_count": 1,
                    "retry_count": 0,
                },
            }
            return {**latest, "auto_suggestion": status}

        second_repo.update(session_id, reserve)

    def interleaving_replace(updated):
        inject_reservation_once()
        return original_replace(updated)

    def interleaving_update(target_session_id, mutator):
        inject_reservation_once()
        return original_update(target_session_id, mutator)

    monkeypatch.setattr(first_repo, "replace", interleaving_replace)
    monkeypatch.setattr(first_repo, "update", interleaving_update)

    paused = first_client.patch(
        f"/live/asr/sessions/{session_id}/auto-suggestions/status",
        json={"paused": True},
    )
    after_pause = second_repo.get(session_id)
    candidate_id = _execution_candidate_ids(after_pause)[0]
    reservation = after_pause["auto_suggestion"]["candidate_reservations"][
        candidate_id
    ]

    assert paused.status_code == 200
    assert paused.json()["status"]["paused"] is True
    assert reservation["claim_id"] == "cross_app_claim"
    assert reservation["status"] == "retry_pending"

    resumed = first_client.patch(
        f"/live/asr/sessions/{session_id}/auto-suggestions/status",
        json={"paused": False},
    )
    generated = second_client.post(
        f"/live/asr/sessions/{session_id}/auto-suggestions/run-once"
    )
    duplicate = first_client.post(
        f"/live/asr/sessions/{session_id}/auto-suggestions/run-once"
    )
    persisted = second_repo.get(session_id)

    assert resumed.status_code == 200
    assert generated.status_code == 200
    assert generated.json()["generated_card_count"] == 1
    assert duplicate.status_code == 200
    assert duplicate.json()["generated_card_count"] == 0
    assert calls == [candidate_id]
    assert len(persisted["suggestion_cards"]) == 1
    assert (
        persisted["auto_suggestion"]["candidate_reservations"][candidate_id][
            "status"
        ]
        == "completed"
    )


def test_auto_suggestions_run_once_generates_card_and_persists_state(monkeypatch):
    calls = []
    _fake_llm(monkeypatch, calls)
    client = TestClient(create_app())
    _create_realtime_session(monkeypatch, client, "auto_run_once")

    response = client.post("/live/asr/sessions/auto_run_once/auto-suggestions/run-once")
    fetched = client.get("/live/asr/sessions/auto_run_once/events")

    assert response.status_code == 200
    body = response.json()
    assert body["generated_card_count"] == 1
    assert body["status"]["status"] == "running"
    assert body["status"]["processed_candidate_count"] == 1
    assert body["status"]["last_triggered_at_ms"] > 0
    assert len(calls) == 1
    record = fetched.json()
    assert len(record["suggestion_cards"]) == 1
    assert record["suggestion_cards"][0]["evidence_span_ids"]
    assert len(record["auto_suggestion"]["processed_candidate_ids"]) == 1
    assert record["auto_suggestion"]["processed_candidate_ids"][0].startswith("asr_suggestion_candidate_")
    assert body["runs"][0]["transcript_correction_outcome"] == "no_revision_needed"
    correction_status = record["realtime_transcript_correction"]
    assert correction_status["status"] == "combined_no_revision_needed"
    assert correction_status["processed_segment_ids"]
    assert correction_status.get("combined_attempted_segment_ids")


def test_auto_suggestions_missing_provider_config_returns_structured_503(
    monkeypatch,
):
    class NeverConstructedClient:
        def __init__(self):
            raise AssertionError("provider client must not be constructed without config")

    monkeypatch.setattr(
        llm_service.LlmConfig,
        "from_env",
        classmethod(lambda cls: None),
    )
    monkeypatch.setattr(llm_service, "HttpxLlmClient", NeverConstructedClient)
    client = TestClient(create_app())
    session_id = "auto_provider_not_configured"
    _create_realtime_session(monkeypatch, client, session_id, confidence=0.95)

    response = client.post(
        f"/live/asr/sessions/{session_id}/auto-suggestions/run-once"
    )

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "error": "llm_provider_not_configured",
        "purpose": "auto_suggestion",
    }


def test_auto_suggestion_keeps_card_but_disables_combined_correction_by_setting(monkeypatch):
    record, previews = _auto_record_with_candidates(at_ms_values=(31_000,))
    original_text, evidence_span_id = _attach_final_evidence(record, previews[0])

    def execute_candidate(preview, config):
        return {
            **preview,
            "run_status": "completed",
            "card": {
                "card_id": "card_candidate_1",
                "suggestion_text": "建议确认回滚负责人。",
            },
            "llm_usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            },
            "transcript_correction": {
                "segment_id": "seg_1",
                "evidence_span_id": evidence_span_id,
                "original_text": original_text,
                "corrected_text": "接口先灰度 5%，如果 P99 延迟超过 900 毫秒就回滚。",
                "source": "combined_suggestion",
            },
        }

    monkeypatch.setattr(
        auto_suggestion_orchestrator.llm_service,
        "execute_candidate",
        execute_candidate,
    )

    updated, _status, runs = auto_suggestion_orchestrator.run_once(
        record,
        previews=previews,
        config=llm_service.LlmConfig(
            base_url="https://gw.example",
            api_key="sk-x",
            model="m1",
        ),
        acceptance_blockers=[],
        correction_enabled=False,
    )

    assert len(updated["suggestion_cards"]) == 1
    assert not any(
        event.get("event_type") == "transcript_revision"
        for event in updated["events"]
    )
    assert "transcript_revision" not in runs[0]
    assert (
        runs[0]["transcript_correction_outcome"]
        == "correction_disabled_by_setting"
    )
    correction_status = updated["realtime_transcript_correction"]
    assert correction_status["status"] == "correction_disabled_by_setting"
    assert correction_status["processed_segment_ids"] == []


def test_auto_suggestion_combined_call_persists_valid_revision_with_card(monkeypatch):
    record, previews = _auto_record_with_candidates(at_ms_values=(31_000,))
    record["events"].insert(0, {
        "id": "transcript_final:seg_1",
        "event_type": "transcript_final",
        "at_ms": 30_000,
        "payload": {
            "segment_id": "seg_1",
            "text": "接口先恢度百分之五，如果 P 九九延迟超过九百毫秒就回滚。",
            "normalized_text": "接口先恢度百分之五，如果 P 九九延迟超过九百毫秒就回滚。",
            "start_ms": 0,
            "end_ms": 30_000,
            "confidence": 0.9,
            "evidence_spans": [{
                "id": "asr_ev_seg_1",
                "segment_id": "seg_1",
                "quote": "接口先恢度百分之五，如果 P 九九延迟超过九百毫秒就回滚。",
                "start_ms": 0,
                "end_ms": 30_000,
                "status": "active",
            }],
        },
    })

    def execute_candidate(preview, config):
        return {
            **preview,
            "run_status": "completed",
            "card": {"card_id": "card_candidate_1", "suggestion_text": "建议确认回滚负责人。"},
            "llm_usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "transcript_correction": {
                "segment_id": "seg_1",
                "evidence_span_id": "asr_ev_seg_1",
                "original_text": "接口先恢度百分之五，如果 P 九九延迟超过九百毫秒就回滚。",
                "corrected_text": "接口先灰度 5%，如果 P99 延迟超过 900 毫秒就回滚。",
                "source": "combined_suggestion",
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
        }

    monkeypatch.setattr(auto_suggestion_orchestrator.llm_service, "execute_candidate", execute_candidate)

    updated, _status, runs = auto_suggestion_orchestrator.run_once(
        record,
        previews=previews,
        config=llm_service.LlmConfig(base_url="https://gw.example", api_key="sk-x", model="m1"),
        acceptance_blockers=[],
        correction_enabled=True,
    )

    revisions = [event for event in updated["events"] if event.get("event_type") == "transcript_revision"]
    assert len(updated["suggestion_cards"]) == 1
    assert len(revisions) == 1
    assert revisions[0]["payload"]["supersedes_segment_id"] == "seg_1"
    assert runs[0]["transcript_revision"]["id"] == revisions[0]["id"]
    assert updated["realtime_transcript_correction"]["revised_segment_ids"] == ["seg_1"]


def test_auto_suggestion_keeps_card_when_combined_correction_is_invalid(monkeypatch):
    record, previews = _auto_record_with_candidates(at_ms_values=(31_000,))

    def execute_candidate(preview, config):
        return {
            **preview,
            "run_status": "completed",
            "card": {"card_id": "card_candidate_1", "suggestion_text": "建议确认负责人。"},
            "transcript_correction": {
                "segment_id": "missing_segment",
                "corrected_text": "完全无关的改写",
                "source": "combined_suggestion",
                "usage": {"total_tokens": 1},
            },
        }

    monkeypatch.setattr(auto_suggestion_orchestrator.llm_service, "execute_candidate", execute_candidate)

    updated, _status, runs = auto_suggestion_orchestrator.run_once(
        record,
        previews=previews,
        config=llm_service.LlmConfig(base_url="https://gw.example", api_key="sk-x", model="m1"),
        acceptance_blockers=[],
        correction_enabled=True,
    )

    assert len(updated["suggestion_cards"]) == 1
    assert not any(event.get("event_type") == "transcript_revision" for event in updated["events"])
    assert "transcript_revision" not in runs[0]


def test_combined_call_without_correction_marks_segment_no_revision_and_blocks_fallback(monkeypatch):
    record, previews = _auto_record_with_candidates(at_ms_values=(31_000,))
    _attach_final_evidence(record, previews[0])

    def execute_candidate(preview, config):
        return {
            **preview,
            "run_status": "completed",
            "card": {"card_id": "card_candidate_1", "suggestion_text": "建议确认负责人。"},
            "llm_usage": {"total_tokens": 1},
        }

    monkeypatch.setattr(auto_suggestion_orchestrator.llm_service, "execute_candidate", execute_candidate)

    updated, _status, _runs = auto_suggestion_orchestrator.run_once(
        record,
        previews=previews,
        config=llm_service.LlmConfig(base_url="https://gw.example", api_key="sk-x", model="m1"),
        acceptance_blockers=[],
        correction_enabled=True,
    )

    correction_status = updated["realtime_transcript_correction"]
    assert correction_status["combined_attempted_segment_ids"] == ["seg_1"]
    assert correction_status["combined_no_revision_needed_segment_ids"] == ["seg_1"]
    assert correction_status["processed_segment_ids"] == ["seg_1"]
    assert realtime_transcript_correction.eligible_final_batch(updated, force=True)["reason"] == "no_unrevised_final"


def test_combined_call_safety_rejection_marks_segment_rejected_and_blocks_fallback(monkeypatch):
    record, previews = _auto_record_with_candidates(at_ms_values=(31_000,))
    original_text, evidence_span_id = _attach_final_evidence(record, previews[0])

    def execute_candidate(preview, config):
        return {
            **preview,
            "run_status": "completed",
            "card": {"card_id": "card_candidate_1", "suggestion_text": "建议确认负责人。"},
            "llm_usage": {"total_tokens": 1},
            "transcript_correction": {
                "segment_id": "seg_1",
                "evidence_span_id": evidence_span_id,
                "original_text": original_text,
                "corrected_text": "完全无关的改写",
                "source": "combined_suggestion",
            },
        }

    monkeypatch.setattr(auto_suggestion_orchestrator.llm_service, "execute_candidate", execute_candidate)

    updated, _status, runs = auto_suggestion_orchestrator.run_once(
        record,
        previews=previews,
        config=llm_service.LlmConfig(base_url="https://gw.example", api_key="sk-x", model="m1"),
        acceptance_blockers=[],
        correction_enabled=True,
    )

    correction_status = updated["realtime_transcript_correction"]
    assert "transcript_revision" not in runs[0]
    assert correction_status["combined_attempted_segment_ids"] == ["seg_1"]
    assert correction_status["combined_rejected_segment_ids"] == ["seg_1"]
    assert correction_status["processed_segment_ids"] == ["seg_1"]
    assert realtime_transcript_correction.eligible_final_batch(updated, force=True)["reason"] == "no_unrevised_final"


def test_auto_suggestions_live_final_can_generate_card_before_end(monkeypatch, tmp_path):
    calls = []
    _fake_llm(monkeypatch, calls)
    monkeypatch.setattr(asr_stream, "get_recognizer", lambda sid: _LiveFinalRecognizer(sid))
    monkeypatch.setattr(asr_stream, "_correct_transcript", lambda raw, cfg: (raw, {"total_tokens": 0}, False))
    client = TestClient(create_app(data_dir=tmp_path))
    sid = "auto_live_before_end"

    with client.websocket_connect(f"/live/asr/stream/ws/{sid}?audio_source=browser_live_mic") as ws:
        ws.send_bytes(b"\x00" * 3200)
        final = json.loads(ws.receive_text())
        assert final["event_type"] == "final"

        snapshot = client.get(f"/live/asr/sessions/{sid}/events")
        assert snapshot.status_code == 200
        assert snapshot.json()["event_source"]["acceptance_eligible"] is True

        response = client.post(f"/live/asr/sessions/{sid}/auto-suggestions/run-once")
        assert response.status_code == 200
        assert response.json()["generated_card_count"] == 1
        assert len(calls) == 1

        ws.send_text("END")
        ended = _receive_until_event(ws, "final")
        assert ended["event_type"] == "final"

    fetched = client.get(f"/live/asr/sessions/{sid}/events").json()
    assert len(fetched["suggestion_cards"]) == 1
    assert fetched["auto_suggestion"]["processed_candidate_count"] == 1


def test_auto_suggestions_block_mixed_language_asr_before_provider_call(monkeypatch, tmp_path):
    calls = []
    _fake_llm(monkeypatch, calls)
    monkeypatch.setattr(asr_stream, "get_recognizer", lambda sid: _MixedLanguageNoiseRecognizer(sid))
    monkeypatch.setattr(asr_stream, "_correct_transcript", lambda raw, cfg: (raw, {"total_tokens": 0}, False))
    client = TestClient(create_app(data_dir=tmp_path))
    sid = "auto_mixed_language_noise"

    with client.websocket_connect(f"/live/asr/stream/ws/{sid}?audio_source=browser_live_mic") as ws:
        ws.send_bytes(b"\x00" * 3200)
        ws.receive_text()
        ws.send_text("END")
        final = _receive_until_event(ws, "final")
        assert final["event_type"] == "final"

    events = client.get(f"/live/asr/sessions/{sid}/events").json()
    assert events["event_source"]["asr_semantic_quality"]["status"] == "blocked"
    assert "mixed_language_fragmentation" in events["event_source"]["asr_semantic_quality"]["quality_failure_reasons"]
    assert "asr_semantic_quality_blocked" in events["event_source"]["acceptance_blockers"]

    response = client.post(f"/live/asr/sessions/{sid}/auto-suggestions/run-once")

    assert response.status_code == 200
    body = response.json()
    assert body["generated_card_count"] == 0
    assert body["reason"] == "acceptance_blocked"
    assert body["suggestion_cards"] == []
    assert calls == []


def test_auto_suggestion_pause_resume_works_during_live_recording(monkeypatch, tmp_path):
    calls = []
    _fake_llm(monkeypatch, calls)
    monkeypatch.setattr(asr_stream, "get_recognizer", lambda sid: _LiveFinalRecognizer(sid))
    monkeypatch.setattr(asr_stream, "_correct_transcript", lambda raw, cfg: (raw, {"total_tokens": 0}, False))
    client = TestClient(create_app(data_dir=tmp_path))
    sid = "auto_live_pause_resume"

    with client.websocket_connect(f"/live/asr/stream/ws/{sid}?audio_source=browser_live_mic") as ws:
        ws.send_bytes(b"\x00" * 3200)
        final = json.loads(ws.receive_text())
        assert final["event_type"] == "final"

        pause = client.patch(f"/live/asr/sessions/{sid}/auto-suggestions/status", json={"paused": True})
        assert pause.status_code == 200
        assert pause.json()["status"]["paused"] is True

        blocked = client.post(f"/live/asr/sessions/{sid}/auto-suggestions/run-once")
        assert blocked.status_code == 200
        assert blocked.json()["generated_card_count"] == 0
        assert blocked.json()["status"]["suppressed"][-1]["reason"] == "paused"
        assert len(calls) == 0

        resume = client.patch(f"/live/asr/sessions/{sid}/auto-suggestions/status", json={"paused": False})
        assert resume.status_code == 200

        generated = client.post(f"/live/asr/sessions/{sid}/auto-suggestions/run-once")
        assert generated.status_code == 200
        assert generated.json()["generated_card_count"] == 1
        assert len(calls) == 1

        ws.send_text("END")
        ended = _receive_until_event(ws, "final")
        assert ended["event_type"] == "final"


def test_auto_suggestions_run_once_is_idempotent_for_same_candidate(monkeypatch):
    calls = []
    _fake_llm(monkeypatch, calls)
    client = TestClient(create_app())
    _create_realtime_session(monkeypatch, client, "auto_idempotent")

    first = client.post("/live/asr/sessions/auto_idempotent/auto-suggestions/run-once")
    second = client.post("/live/asr/sessions/auto_idempotent/auto-suggestions/run-once")
    fetched = client.get("/live/asr/sessions/auto_idempotent/events").json()

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["generated_card_count"] == 1
    assert second.json()["generated_card_count"] == 0
    assert second.json()["suppressed_count"] >= 1
    assert second.json()["status"]["suppressed"][-1]["reason"] == "duplicate"
    assert len(calls) == 1
    assert len(fetched["suggestion_cards"]) == 1


def test_auto_suggestions_suppresses_low_confidence_without_llm_call(monkeypatch):
    calls = []
    _fake_llm(monkeypatch, calls)
    client = TestClient(create_app())
    _create_realtime_session(monkeypatch, client, "auto_low_conf", confidence=0.5)

    response = client.post("/live/asr/sessions/auto_low_conf/auto-suggestions/run-once")
    fetched = client.get("/live/asr/sessions/auto_low_conf/events").json()

    assert response.status_code == 200
    assert response.json()["generated_card_count"] == 0
    assert response.json()["status"]["suppressed"][-1]["reason"] == "low_confidence"
    assert len(calls) == 0
    assert fetched["suggestion_cards"] == []


def test_runtime_policy_user_confidence_threshold_blocks_candidate_before_provider(
    monkeypatch,
):
    calls = []
    _fake_candidate_executor(monkeypatch, calls)
    record, previews = _auto_record_with_candidates(at_ms_values=(31_000,))
    record = _apply_runtime_policy(record, confidence_threshold=0.95)

    updated, status, runs = auto_suggestion_orchestrator.run_once(
        record,
        previews=previews,
        config=llm_service.LlmConfig(
            base_url="https://gw.example",
            api_key="sk-test",
            model="m1",
        ),
        acceptance_blockers=[],
    )

    assert calls == []
    assert runs == []
    assert updated["suggestion_cards"] == []
    assert status["effective_policy"]["effective_confidence_threshold"] == 0.95
    assert status["last_suppression_reason"] == "low_confidence"


def test_runtime_policy_missing_candidate_confidence_is_suppressed(monkeypatch):
    calls = []
    _fake_candidate_executor(monkeypatch, calls)
    record, previews = _auto_record_with_candidates(at_ms_values=(31_000,))
    previews[0].pop("candidate_confidence")
    record["events"][0]["payload"].pop("confidence")
    record = _apply_runtime_policy(record, confidence_threshold=0.7)

    updated, status, runs = auto_suggestion_orchestrator.run_once(
        record,
        previews=previews,
        config=llm_service.LlmConfig(
            base_url="https://gw.example",
            api_key="sk-test",
            model="m1",
        ),
        acceptance_blockers=[],
    )

    assert calls == []
    assert runs == []
    assert updated["suggestion_cards"] == []
    assert status["last_suppression_reason"] == "low_confidence"


def test_level_zero_and_one_apply_expected_candidate_confidence_matrix(monkeypatch):
    config = llm_service.LlmConfig(
        base_url="https://gw.example",
        api_key="sk-test",
        model="m1",
    )

    level_zero_calls = []
    _fake_candidate_executor(monkeypatch, level_zero_calls)
    level_zero_record, level_zero_previews = _auto_record_with_candidates(
        at_ms_values=(31_000,)
    )
    level_zero_previews[0]["candidate_confidence"] = 0.89
    level_zero_record["events"][0]["payload"]["confidence"] = 0.89
    level_zero_record = _apply_runtime_policy(
        level_zero_record,
        confidence_threshold=0.7,
        degradation_level=0,
    )

    level_zero_updated, level_zero_status, level_zero_runs = (
        auto_suggestion_orchestrator.run_once(
            level_zero_record,
            previews=level_zero_previews,
            config=config,
            acceptance_blockers=[],
        )
    )

    assert level_zero_calls == ["candidate_1"]
    assert len(level_zero_runs) == 1
    assert len(level_zero_updated["suggestion_cards"]) == 1
    assert level_zero_status["effective_policy"]["effective_confidence_threshold"] == 0.7

    level_one_calls = []
    _fake_candidate_executor(monkeypatch, level_one_calls)
    level_one_record, level_one_previews = _auto_record_with_candidates(
        at_ms_values=(31_000,)
    )
    level_one_previews[0]["candidate_confidence"] = 0.89
    level_one_record["events"][0]["payload"]["confidence"] = 0.89
    level_one_record = _apply_runtime_policy(
        level_one_record,
        confidence_threshold=0.7,
        degradation_level=1,
    )

    level_one_updated, level_one_status, level_one_runs = (
        auto_suggestion_orchestrator.run_once(
            level_one_record,
            previews=level_one_previews,
            config=config,
            acceptance_blockers=[],
        )
    )

    assert level_one_calls == []
    assert level_one_runs == []
    assert level_one_updated["suggestion_cards"] == []
    assert level_one_status["effective_policy"]["effective_confidence_threshold"] == 0.9
    assert level_one_status["last_suppression_reason"] == "low_confidence"


def test_level_one_filters_low_confidence_provider_result_but_keeps_usage(monkeypatch):
    calls = []
    record, previews = _auto_record_with_candidates(at_ms_values=(31_000,))
    previews[0]["candidate_confidence"] = 0.95
    record["events"][0]["payload"]["confidence"] = 0.95
    record = _apply_runtime_policy(
        record,
        confidence_threshold=0.7,
        degradation_level=1,
    )

    def execute_candidate(preview, config):
        calls.append(preview["target_candidate_id"])
        return {
            **preview,
            "run_status": "completed",
            "card": {
                "card_id": "level_one_low_result",
                "suggestion_text": "建议确认回滚 owner。",
                "confidence": 0.89,
            },
            "llm_usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            },
        }

    monkeypatch.setattr(
        auto_suggestion_orchestrator.llm_service,
        "execute_candidate",
        execute_candidate,
    )

    updated, status, runs = auto_suggestion_orchestrator.run_once(
        record,
        previews=previews,
        config=llm_service.LlmConfig(
            base_url="https://gw.example",
            api_key="sk-test",
            model="m1",
        ),
        acceptance_blockers=[],
    )

    assert calls == ["candidate_1"]
    assert updated["suggestion_cards"] == []
    assert runs[0]["llm_usage"]["total_tokens"] == 15
    assert runs[0]["card_status"] == "suppressed"
    assert runs[0]["suppression_reason"] == "generated_low_confidence"
    assert status["last_suppression_reason"] == "generated_low_confidence"


def test_level_zero_filters_provider_result_below_user_threshold_and_keeps_usage(
    monkeypatch,
):
    calls = []
    record, previews = _auto_record_with_candidates(at_ms_values=(31_000,))
    previews[0]["candidate_confidence"] = 0.95
    record["events"][0]["payload"]["confidence"] = 0.95
    record = _apply_runtime_policy(
        record,
        confidence_threshold=0.9,
        degradation_level=0,
    )

    def execute_candidate(preview, config):
        calls.append(preview["target_candidate_id"])
        return {
            **preview,
            "run_status": "completed",
            "card": {
                "card_id": "level_zero_low_result",
                "suggestion_text": "建议确认回滚 owner。",
                "confidence": 0.89,
            },
            "llm_usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            },
        }

    monkeypatch.setattr(
        auto_suggestion_orchestrator.llm_service,
        "execute_candidate",
        execute_candidate,
    )

    updated, status, runs = auto_suggestion_orchestrator.run_once(
        record,
        previews=previews,
        config=llm_service.LlmConfig(
            base_url="https://gw.example",
            api_key="sk-test",
            model="m1",
        ),
        acceptance_blockers=[],
    )

    assert calls == ["candidate_1"]
    assert updated["suggestion_cards"] == []
    assert runs[0]["llm_usage"]["total_tokens"] == 15
    assert runs[0]["card_status"] == "suppressed"
    assert runs[0]["suppression_reason"] == "generated_low_confidence"
    assert runs[0]["suppressed_card"]["confidence"] == 0.89
    assert status["effective_policy"]["effective_confidence_threshold"] == 0.9
    assert status["last_suppression_reason"] == "generated_low_confidence"
    assert status["suppressed"][-1]["llm_usage"]["total_tokens"] == 15


def test_runtime_policy_window_is_server_enforced_without_suppressing_first_request(
    monkeypatch,
):
    calls = []
    _fake_candidate_executor(monkeypatch, calls)
    now_ms = {"value": 1_000_000}
    monkeypatch.setattr(auto_suggestion_orchestrator, "_wall_clock_ms", lambda: now_ms["value"])
    record, previews = _auto_record_with_candidates(at_ms_values=(1_000, 2_000))
    record = _apply_runtime_policy(
        record,
        window_seconds=20,
        cooldown_minutes=0,
    )

    first, first_status, first_runs = auto_suggestion_orchestrator.run_once(
        record,
        previews=previews,
        config=llm_service.LlmConfig(
            base_url="https://gw.example",
            api_key="sk-test",
            model="m1",
        ),
        acceptance_blockers=[],
    )
    now_ms["value"] += 1_000
    second, second_status, second_runs = auto_suggestion_orchestrator.run_once(
        first,
        previews=previews,
        config=llm_service.LlmConfig(
            base_url="https://gw.example",
            api_key="sk-test",
            model="m1",
        ),
        acceptance_blockers=[],
    )

    assert [run["target_candidate_id"] for run in first_runs] == ["candidate_1"]
    assert first_status["last_evaluated_at_ms"] == 1_000_000
    assert [item["reason"] for item in first_status["suppressed"]] == []
    assert second_runs == []
    assert calls == ["candidate_1"]
    assert second["suggestion_cards"] == first["suggestion_cards"]
    assert second_status["last_suppression_reason"] == "evaluation_window"


def test_runtime_policy_cooldown_applies_only_after_successful_card(monkeypatch):
    calls = []
    _fake_candidate_executor(monkeypatch, calls)
    now_ms = {"value": 1_000_000}
    monkeypatch.setattr(auto_suggestion_orchestrator, "_wall_clock_ms", lambda: now_ms["value"])
    record, previews = _auto_record_with_candidates(at_ms_values=(1_000, 2_000))
    record = _apply_runtime_policy(
        record,
        window_seconds=1,
        cooldown_minutes=2,
    )

    first, _first_status, first_runs = auto_suggestion_orchestrator.run_once(
        record,
        previews=previews,
        config=llm_service.LlmConfig(
            base_url="https://gw.example",
            api_key="sk-test",
            model="m1",
        ),
        acceptance_blockers=[],
    )
    now_ms["value"] += 61_000
    _second, second_status, second_runs = auto_suggestion_orchestrator.run_once(
        first,
        previews=previews,
        config=llm_service.LlmConfig(
            base_url="https://gw.example",
            api_key="sk-test",
            model="m1",
        ),
        acceptance_blockers=[],
    )

    assert [run["target_candidate_id"] for run in first_runs] == ["candidate_1"]
    assert second_runs == []
    assert calls == ["candidate_1"]
    assert second_status["last_suppression_reason"] == "cooldown"


def test_provider_failures_do_not_start_cooldown_for_next_candidate(monkeypatch):
    calls = []
    now_ms = {"value": 1_000_000}
    monkeypatch.setattr(auto_suggestion_orchestrator, "_wall_clock_ms", lambda: now_ms["value"])
    record, previews = _auto_record_with_candidates(at_ms_values=(1_000, 2_000))
    record = _apply_runtime_policy(
        record,
        window_seconds=1,
        cooldown_minutes=5,
    )

    def execute_candidate(preview, config):
        candidate_id = preview["target_candidate_id"]
        calls.append(candidate_id)
        if candidate_id == "candidate_1":
            raise RuntimeError("provider unavailable")
        return {
            **preview,
            "run_status": "completed",
            "card": {
                "card_id": "card_candidate_2",
                "suggestion_text": "建议确认 owner。",
                "confidence": 0.95,
            },
            "llm_usage": {"total_tokens": 1},
        }

    monkeypatch.setattr(
        auto_suggestion_orchestrator.llm_service,
        "execute_candidate",
        execute_candidate,
    )
    config = llm_service.LlmConfig(
        base_url="https://gw.example",
        api_key="sk-test",
        model="m1",
    )

    first, first_status, _first_runs = auto_suggestion_orchestrator.run_once(
        record,
        previews=previews,
        config=config,
        acceptance_blockers=[],
    )
    now_ms["value"] += 1_000
    second, second_status, _second_runs = auto_suggestion_orchestrator.run_once(
        first,
        previews=previews,
        config=config,
        acceptance_blockers=[],
    )
    now_ms["value"] += 1_000
    third, third_status, third_runs = auto_suggestion_orchestrator.run_once(
        second,
        previews=previews,
        config=config,
        acceptance_blockers=[],
    )

    assert first_status["last_triggered_at_ms"] == 0
    assert first_status["last_successful_card_at_ms"] == 0
    assert second_status["candidate_reservations"]["candidate_1"]["status"] == "terminal_failed"
    assert second_status["last_triggered_at_ms"] == 0
    assert second_status["last_successful_card_at_ms"] == 0
    assert [run["target_candidate_id"] for run in third_runs] == ["candidate_2"]
    assert len(third["suggestion_cards"]) == 1
    assert third_status["last_triggered_at_ms"] == 1_002_000
    assert calls == ["candidate_1", "candidate_1", "candidate_2"]


def _assert_low_output_does_not_start_cooldown(
    monkeypatch,
    *,
    degradation_level,
    confidence_threshold,
):
    calls = []
    now_ms = {"value": 2_000_000}
    monkeypatch.setattr(auto_suggestion_orchestrator, "_wall_clock_ms", lambda: now_ms["value"])
    record, previews = _auto_record_with_candidates(at_ms_values=(1_000, 2_000))
    for preview in previews:
        preview["candidate_confidence"] = 0.95
    for event in record["events"]:
        event["payload"]["confidence"] = 0.95
    record = _apply_runtime_policy(
        record,
        confidence_threshold=confidence_threshold,
        window_seconds=1,
        cooldown_minutes=5,
        degradation_level=degradation_level,
    )

    def execute_candidate(preview, config):
        candidate_id = preview["target_candidate_id"]
        calls.append(candidate_id)
        return {
            **preview,
            "run_status": "completed",
            "card": {
                "card_id": f"card_{candidate_id}",
                "suggestion_text": "建议确认 owner。",
                "confidence": 0.89 if candidate_id == "candidate_1" else 0.95,
            },
            "llm_usage": {"total_tokens": 1},
        }

    monkeypatch.setattr(
        auto_suggestion_orchestrator.llm_service,
        "execute_candidate",
        execute_candidate,
    )
    config = llm_service.LlmConfig(
        base_url="https://gw.example",
        api_key="sk-test",
        model="m1",
    )

    first, first_status, first_runs = auto_suggestion_orchestrator.run_once(
        record,
        previews=previews,
        config=config,
        acceptance_blockers=[],
    )
    now_ms["value"] += 1_000
    second, second_status, second_runs = auto_suggestion_orchestrator.run_once(
        first,
        previews=previews,
        config=config,
        acceptance_blockers=[],
    )

    assert first_runs[0]["card_status"] == "suppressed"
    assert first_status["last_triggered_at_ms"] == 0
    assert first_status["last_successful_card_at_ms"] == 0
    assert [run["target_candidate_id"] for run in second_runs] == ["candidate_2"]
    assert len(second["suggestion_cards"]) == 1
    assert second_status["last_triggered_at_ms"] == 2_001_000
    assert calls == ["candidate_1", "candidate_2"]


def test_level_zero_low_output_does_not_start_cooldown(monkeypatch):
    _assert_low_output_does_not_start_cooldown(
        monkeypatch,
        degradation_level=0,
        confidence_threshold=0.9,
    )


def test_level_one_low_output_does_not_start_cooldown(monkeypatch):
    _assert_low_output_does_not_start_cooldown(
        monkeypatch,
        degradation_level=1,
        confidence_threshold=0.7,
    )


def test_completed_without_card_does_not_start_cooldown(monkeypatch):
    calls = []
    now_ms = {"value": 3_000_000}
    monkeypatch.setattr(auto_suggestion_orchestrator, "_wall_clock_ms", lambda: now_ms["value"])
    record, previews = _auto_record_with_candidates(at_ms_values=(1_000, 2_000))
    record = _apply_runtime_policy(
        record,
        window_seconds=1,
        cooldown_minutes=5,
    )

    def execute_candidate(preview, config):
        candidate_id = preview["target_candidate_id"]
        calls.append(candidate_id)
        if candidate_id == "candidate_1":
            return {
                **preview,
                "run_status": "completed",
                "card_status": "not_created",
                "llm_usage": {"total_tokens": 1},
            }
        return {
            **preview,
            "run_status": "completed",
            "card": {
                "card_id": "card_candidate_2",
                "suggestion_text": "建议确认 owner。",
                "confidence": 0.95,
            },
            "llm_usage": {"total_tokens": 1},
        }

    monkeypatch.setattr(
        auto_suggestion_orchestrator.llm_service,
        "execute_candidate",
        execute_candidate,
    )
    config = llm_service.LlmConfig(
        base_url="https://gw.example",
        api_key="sk-test",
        model="m1",
    )

    first, first_status, first_runs = auto_suggestion_orchestrator.run_once(
        record,
        previews=previews,
        config=config,
        acceptance_blockers=[],
    )
    now_ms["value"] += 1_000
    second, second_status, second_runs = auto_suggestion_orchestrator.run_once(
        first,
        previews=previews,
        config=config,
        acceptance_blockers=[],
    )

    assert first_runs[0]["card_status"] == "not_created"
    assert first_status["last_triggered_at_ms"] == 0
    assert first_status["last_successful_card_at_ms"] == 0
    assert [run["target_candidate_id"] for run in second_runs] == ["candidate_2"]
    assert len(second["suggestion_cards"]) == 1
    assert second_status["last_triggered_at_ms"] == 3_001_000
    assert calls == ["candidate_1", "candidate_2"]


def test_unexpired_reservation_lease_remains_single_flight():
    record, previews = _auto_record_with_candidates(at_ms_values=(31_000,))
    record = _apply_runtime_policy(record)
    record["auto_suggestion"].update({
        "candidate_attempt_counts": {"candidate_1": 1},
        "candidate_reservations": {
            "candidate_1": {
                "candidate_id": "candidate_1",
                "claim_id": "existing_claim",
                "status": "reserved",
                "reserved_at_ms": 10_000,
                "attempt_count": 1,
                "retry_count": 0,
            },
        },
    })

    updated, status, claim = auto_suggestion_orchestrator.claim_candidate(
        record,
        previews=previews,
        config=llm_service.LlmConfig(
            base_url="https://gw.example",
            api_key="sk-test",
            model="m1",
        ),
        acceptance_blockers=[],
        claim_id="new_claim",
        now_ms=69_999,
    )

    assert claim is None
    assert updated["suggestion_cards"] == []
    assert status["candidate_reservations"]["candidate_1"]["claim_id"] == "existing_claim"
    assert status["last_suppression_reason"] == "in_flight"


def test_expired_reservation_lease_is_reclaimed_with_bounded_attempt(monkeypatch):
    record, previews = _auto_record_with_candidates(at_ms_values=(31_000,))
    record = _apply_runtime_policy(record)
    record["auto_suggestion"].update({
        "candidate_attempt_counts": {"candidate_1": 1},
        "candidate_reservations": {
            "candidate_1": {
                "candidate_id": "candidate_1",
                "claim_id": "stale_claim",
                "status": "reserved",
                "reserved_at_ms": 10_000,
                "attempt_count": 1,
                "retry_count": 0,
            },
        },
    })

    _updated, status, claim = auto_suggestion_orchestrator.claim_candidate(
        record,
        previews=previews,
        config=llm_service.LlmConfig(
            base_url="https://gw.example",
            api_key="sk-test",
            model="m1",
        ),
        acceptance_blockers=[],
        claim_id="recovery_claim",
        now_ms=70_001,
    )

    assert claim is not None
    assert claim["claim_id"] == "recovery_claim"
    assert claim["attempt_count"] == 2
    assert claim["recovered_from_stale_reservation"] is True
    assert claim["lease_expires_at_ms"] == 130_001
    assert status["last_reservation_recovery_reason"] == "reservation_lease_expired_retry"


def test_expired_reservation_at_attempt_limit_becomes_terminal():
    record, previews = _auto_record_with_candidates(at_ms_values=(31_000,))
    record = _apply_runtime_policy(record)
    record["auto_suggestion"].update({
        "candidate_attempt_counts": {"candidate_1": 2},
        "candidate_reservations": {
            "candidate_1": {
                "candidate_id": "candidate_1",
                "claim_id": "stale_final_claim",
                "status": "reserved",
                "reserved_at_ms": 10_000,
                "attempt_count": 2,
                "retry_count": 1,
            },
        },
    })

    _updated, status, claim = auto_suggestion_orchestrator.claim_candidate(
        record,
        previews=previews,
        config=llm_service.LlmConfig(
            base_url="https://gw.example",
            api_key="sk-test",
            model="m1",
        ),
        acceptance_blockers=[],
        claim_id="must_not_claim",
        now_ms=70_001,
    )

    assert claim is None
    assert status["candidate_reservations"]["candidate_1"]["status"] == "terminal_failed"
    assert status["processed_candidate_ids"] == ["candidate_1"]
    assert status["terminal_failed_candidate_ids"] == ["candidate_1"]
    assert status["last_suppression_reason"] == "reservation_lease_expired_terminal"


def test_two_sqlite_repositories_atomically_reclaim_one_stale_reservation(
    monkeypatch,
    tmp_path,
):
    config = llm_service.LlmConfig(
        base_url="https://gw.example",
        api_key="sk-test",
        model="m1",
    )
    record, previews = _auto_record_with_candidates(at_ms_values=(31_000,))
    record["session_id"] = "sqlite_stale_reclaim"
    record = _apply_runtime_policy(record)
    record["auto_suggestion"].update({
        "candidate_attempt_counts": {"candidate_1": 1},
        "candidate_reservations": {
            "candidate_1": {
                "candidate_id": "candidate_1",
                "claim_id": "stale_claim",
                "status": "reserved",
                "reserved_at_ms": 10_000,
                "lease_expires_at_ms": 70_000,
                "attempt_count": 1,
                "retry_count": 0,
            },
        },
    })
    first_app = create_app(data_dir=tmp_path)
    second_app = create_app(data_dir=tmp_path)
    first_repo = first_app.state.asr_live_repository
    second_repo = second_app.state.asr_live_repository
    first_repo.create(record)
    start = Event()
    provider_entered = Event()
    release_provider = Event()
    calls = []

    def execute_candidate(preview, provider_config):
        calls.append(preview["target_candidate_id"])
        provider_entered.set()
        assert release_provider.wait(timeout=3)
        return {
            **preview,
            "run_status": "completed",
            "card": {
                "card_id": "sqlite_stale_reclaim_card",
                "suggestion_text": "建议确认回滚 owner。",
                "confidence": 0.95,
            },
            "llm_usage": {"total_tokens": 1},
        }

    monkeypatch.setattr(
        auto_suggestion_orchestrator.llm_service,
        "execute_candidate",
        execute_candidate,
    )

    def compete(repository, claim_id):
        assert start.wait(timeout=2)
        claimed = {}

        def claim_latest(latest):
            updated, _status, claim = auto_suggestion_orchestrator.claim_candidate(
                latest,
                previews=previews,
                config=config,
                acceptance_blockers=[],
                claim_id=claim_id,
                now_ms=70_001,
            )
            if claim is not None:
                claimed.update(claim)
            return updated

        claimed_record = repository.update(record["session_id"], claim_latest)
        if not claimed:
            status = auto_suggestion_orchestrator.status_from_record(claimed_record)
            return {
                "claimed": False,
                "reason": status["last_suppression_reason"],
                "claim_id": status["candidate_reservations"]["candidate_1"][
                    "claim_id"
                ],
            }
        _computed, _status, runs = auto_suggestion_orchestrator.run_once(
            claimed_record,
            previews=[dict(claimed["preview"])],
            config=config,
            acceptance_blockers=[],
            claimed_candidate_id="candidate_1",
            claim_id=claim_id,
        )
        return {
            "claimed": True,
            "reason": "reclaimed",
            "claim_id": claim_id,
            "run_count": len(runs),
        }

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first_future = pool.submit(compete, first_repo, "reclaim_one")
            second_future = pool.submit(compete, second_repo, "reclaim_two")
            start.set()
            assert provider_entered.wait(timeout=2)
            time.sleep(0.1)
            release_provider.set()
            results = [
                first_future.result(timeout=5),
                second_future.result(timeout=5),
            ]
    finally:
        release_provider.set()
        for app in (first_app, second_app):
            for repository in reversed(app.state.sqlite_repositories):
                repository.close()

    winners = [result for result in results if result["claimed"]]
    losers = [result for result in results if not result["claimed"]]
    assert len(winners) == 1
    assert winners[0]["run_count"] == 1
    assert len(losers) == 1
    assert losers[0]["reason"] == "in_flight"
    assert losers[0]["claim_id"] == winners[0]["claim_id"]
    assert calls == ["candidate_1"]


def test_auto_suggestion_status_patch_pauses_and_resumes(monkeypatch):
    calls = []
    _fake_llm(monkeypatch, calls)
    client = TestClient(create_app())
    _create_realtime_session(monkeypatch, client, "auto_pause_resume")

    pause = client.patch(
        "/live/asr/sessions/auto_pause_resume/auto-suggestions/status",
        json={"paused": True},
    )
    blocked = client.post("/live/asr/sessions/auto_pause_resume/auto-suggestions/run-once")
    assert blocked.json()["generated_card_count"] == 0
    assert blocked.json()["status"]["suppressed"][-1]["reason"] == "paused"
    assert len(calls) == 0
    resume = client.patch(
        "/live/asr/sessions/auto_pause_resume/auto-suggestions/status",
        json={"paused": False},
    )
    generated = client.post("/live/asr/sessions/auto_pause_resume/auto-suggestions/run-once")

    assert pause.status_code == 200
    assert pause.json()["status"]["paused"] is True
    assert resume.status_code == 200
    assert resume.json()["status"]["paused"] is False
    assert generated.json()["generated_card_count"] == 1
    assert len(calls) == 1


def test_auto_suggestion_orchestrator_suppresses_second_candidate_inside_cooldown(monkeypatch):
    calls = []
    _fake_candidate_executor(monkeypatch, calls)
    record, previews = _auto_record_with_candidates(at_ms_values=(1_000, 2_000))

    updated, status, runs = auto_suggestion_orchestrator.run_once(
        record,
        previews=previews,
        config=llm_service.LlmConfig(base_url="https://gw.example", api_key="sk-test", model="m1"),
        acceptance_blockers=[],
    )

    assert [run["target_candidate_id"] for run in runs] == ["candidate_1"]
    assert calls == ["candidate_1"]
    assert len(updated["suggestion_cards"]) == 1
    assert status["suppressed"] == []

    updated_again, status_again, runs_again = auto_suggestion_orchestrator.run_once(
        updated,
        previews=previews,
        config=llm_service.LlmConfig(base_url="https://gw.example", api_key="sk-test", model="m1"),
        acceptance_blockers=[],
    )

    assert runs_again == []
    assert calls == ["candidate_1"]
    assert updated_again["suggestion_cards"] == updated["suggestion_cards"]
    assert status_again["suppressed"][-1]["candidate_id"] == "candidate_2"
    assert status_again["suppressed"][-1]["reason"] == "cooldown"


def test_auto_suggestion_orchestrator_executes_at_most_one_candidate_per_request(monkeypatch):
    calls = []
    _fake_candidate_executor(monkeypatch, calls)
    now_ms = {"value": 1_000_000}
    monkeypatch.setattr(auto_suggestion_orchestrator, "_wall_clock_ms", lambda: now_ms["value"])
    record, previews = _auto_record_with_candidates(at_ms_values=(1_000, 32_000, 63_000))

    updated, status, runs = auto_suggestion_orchestrator.run_once(
        record,
        previews=previews,
        config=llm_service.LlmConfig(base_url="https://gw.example", api_key="sk-test", model="m1"),
        acceptance_blockers=[],
    )

    assert [run["target_candidate_id"] for run in runs] == ["candidate_1"]
    assert calls == ["candidate_1"]
    assert status["processed_candidate_ids"] == ["candidate_1"]
    assert all(item["candidate_id"] != "candidate_2" for item in status["suppressed"])

    now_ms["value"] += 31_000
    updated_again, status_again, runs_again = auto_suggestion_orchestrator.run_once(
        updated,
        previews=previews,
        config=llm_service.LlmConfig(base_url="https://gw.example", api_key="sk-test", model="m1"),
        acceptance_blockers=[],
    )

    assert [run["target_candidate_id"] for run in runs_again] == ["candidate_2"]
    assert calls == ["candidate_1", "candidate_2"]
    assert status_again["processed_candidate_ids"] == ["candidate_1", "candidate_2"]
    assert len(updated_again["suggestion_cards"]) == 2


def test_auto_suggestion_orchestrator_acceptance_blocker_prevents_llm_call(monkeypatch):
    calls = []
    _fake_candidate_executor(monkeypatch, calls)
    record, previews = _auto_record_with_candidates(at_ms_values=(1_000,))

    updated, status, runs = auto_suggestion_orchestrator.run_once(
        record,
        previews=previews,
        config=llm_service.LlmConfig(base_url="https://gw.example", api_key="sk-test", model="m1"),
        acceptance_blockers=["asr_semantic_quality_blocked"],
    )

    assert runs == []
    assert calls == []
    assert updated["suggestion_cards"] == []
    assert status["status"] == "blocked"
    assert status["suppressed"][-1]["reason"] == "acceptance_blocked"


def test_auto_suggestion_rate_limit_uses_sliding_one_hour_window(monkeypatch):
    calls = []
    _fake_candidate_executor(monkeypatch, calls)
    monkeypatch.setattr(auto_suggestion_orchestrator, "_wall_clock_ms", lambda: 3_700_000, raising=False)
    record, previews = _auto_record_with_candidates(
        at_ms_values=(3_700_000,),
        status={
            "max_calls_per_hour": 1,
            "call_timestamps_ms": [1_000],
            "last_triggered_at_ms": 0,
        },
    )

    updated, status, runs = auto_suggestion_orchestrator.run_once(
        record,
        previews=previews,
        config=llm_service.LlmConfig(base_url="https://gw.example", api_key="sk-test", model="m1"),
        acceptance_blockers=[],
    )

    assert len(runs) == 1
    assert calls == ["candidate_1"]
    assert len(updated["suggestion_cards"]) == 1
    assert status["call_timestamps_ms"] == [3_700_000]
    assert status["call_count_last_hour"] == 1


def test_auto_suggestion_cooldown_uses_wall_clock_instead_of_candidate_time(monkeypatch):
    calls = []
    _fake_candidate_executor(monkeypatch, calls)
    monkeypatch.setattr(auto_suggestion_orchestrator, "_wall_clock_ms", lambda: 1_000_000, raising=False)
    record, previews = _auto_record_with_candidates(
        at_ms_values=(9_999_999,),
        status={"last_triggered_at_ms": 990_000, "call_timestamps_ms": [990_000]},
    )

    _updated, status, runs = auto_suggestion_orchestrator.run_once(
        record,
        previews=previews,
        config=llm_service.LlmConfig(base_url="https://gw.example", api_key="sk-test", model="m1"),
        acceptance_blockers=[],
    )

    assert runs == []
    assert calls == []
    assert status["suppressed"][-1]["reason"] == "cooldown"
    assert status["suppressed"][-1]["candidate_at_ms"] == 9_999_999


def test_auto_suggestion_hour_budget_prunes_by_wall_clock_instead_of_candidate_time(monkeypatch):
    calls = []
    _fake_candidate_executor(monkeypatch, calls)
    monkeypatch.setattr(auto_suggestion_orchestrator, "_wall_clock_ms", lambda: 3_700_001, raising=False)
    record, previews = _auto_record_with_candidates(
        at_ms_values=(2_000,),
        status={
            "max_calls_per_hour": 1,
            "call_timestamps_ms": [1_000],
            "last_triggered_at_ms": 1_000,
        },
    )

    _updated, status, runs = auto_suggestion_orchestrator.run_once(
        record,
        previews=previews,
        config=llm_service.LlmConfig(base_url="https://gw.example", api_key="sk-test", model="m1"),
        acceptance_blockers=[],
    )

    assert len(runs) == 1
    assert calls == ["candidate_1"]
    assert status["call_timestamps_ms"] == [3_700_001]


def test_status_bounds_only_display_audit_and_preserves_semantic_state(monkeypatch):
    monkeypatch.setattr(auto_suggestion_orchestrator, "MAX_SUPPRESSED_ENTRIES", 2)
    monkeypatch.setattr(
        auto_suggestion_orchestrator,
        "MAX_TERMINAL_FAILED_CANDIDATE_IDS",
        2,
    )
    monkeypatch.setattr(
        auto_suggestion_orchestrator,
        "MAX_PROCESSED_CANDIDATE_IDS",
        2,
    )
    monkeypatch.setattr(auto_suggestion_orchestrator, "MAX_CANDIDATE_RESERVATIONS", 2)
    monkeypatch.setattr(
        auto_suggestion_orchestrator,
        "MAX_CANDIDATE_ATTEMPT_COUNTS",
        2,
    )
    record = {
        "auto_suggestion": {
            "processed_candidate_ids": ["processed_1", "processed_2", "processed_3"],
            "terminal_failed_candidate_ids": ["processed_1", "processed_2", "processed_3"],
            "candidate_reservations": {
                "active_1": {"candidate_id": "active_1", "status": "reserved"},
                "active_2": {"candidate_id": "active_2", "status": "reserved"},
                "processed_1": {"candidate_id": "processed_1", "status": "completed"},
            },
            "candidate_attempt_counts": {
                "active_1": 1,
                "active_2": 1,
                "processed_1": 2,
            },
            "suppressed": [
                {"candidate_id": f"suppressed_{index}", "reason": "cooldown"}
                for index in range(3)
            ],
        },
    }

    status = auto_suggestion_orchestrator.status_from_record(record)

    assert status["processed_candidate_ids"] == [
        "processed_1",
        "processed_2",
        "processed_3",
    ]
    assert set(status["candidate_reservations"]) >= {"active_1", "active_2"}
    assert set(status["candidate_attempt_counts"]) >= {"active_1", "active_2"}
    assert status["terminal_failed_candidate_ids"] == ["processed_2", "processed_3"]
    assert [item["candidate_id"] for item in status["suppressed"]] == [
        "suppressed_1",
        "suppressed_2",
    ]
    assert status["truncated_counts"] == {
        "terminal_failed_candidate_ids": 1,
        "suppressed": 1,
    }
    assert status["semantic_state_over_capacity"] is True
    assert status["capacity_policy"] == "fail_closed"
    assert status["capacity_limits"] == {
        "processed_candidate_ids": 2,
        "terminal_failed_candidate_ids": 2,
        "candidate_reservations": 2,
        "candidate_attempt_counts": 2,
        "suppressed": 2,
    }


def test_semantic_capacity_fail_closed_preserves_oldest_processed_idempotency(
    monkeypatch,
):
    monkeypatch.setattr(
        auto_suggestion_orchestrator,
        "MAX_PROCESSED_CANDIDATE_IDS",
        2,
    )
    monkeypatch.setattr(auto_suggestion_orchestrator, "MAX_CANDIDATE_RESERVATIONS", 2)
    monkeypatch.setattr(
        auto_suggestion_orchestrator,
        "MAX_CANDIDATE_ATTEMPT_COUNTS",
        2,
    )
    monkeypatch.setattr(
        auto_suggestion_orchestrator,
        "MAX_TERMINAL_FAILED_CANDIDATE_IDS",
        1,
    )
    now_ms = {"value": 1_000_000}
    monkeypatch.setattr(auto_suggestion_orchestrator, "_wall_clock_ms", lambda: now_ms["value"])
    record, previews = _auto_record_with_candidates(at_ms_values=(1_000, 2_000, 3_000))
    record = _apply_runtime_policy(
        record,
        window_seconds=1,
        cooldown_minutes=0,
    )
    calls = []

    def execute_candidate(preview, config):
        candidate_id = preview["target_candidate_id"]
        calls.append(candidate_id)
        if candidate_id == "candidate_1":
            raise RuntimeError("terminal provider failure")
        return {
            **preview,
            "run_status": "completed",
            "card": {
                "card_id": f"card_{candidate_id}",
                "suggestion_text": "建议确认 owner。",
                "confidence": 0.95,
            },
            "llm_usage": {"total_tokens": 1},
        }

    monkeypatch.setattr(
        auto_suggestion_orchestrator.llm_service,
        "execute_candidate",
        execute_candidate,
    )
    config = llm_service.LlmConfig(
        base_url="https://gw.example",
        api_key="sk-test",
        model="m1",
    )

    first, _status, _runs = auto_suggestion_orchestrator.run_once(
        record,
        previews=previews,
        config=config,
        acceptance_blockers=[],
    )
    now_ms["value"] += 1_000
    second, _status, _runs = auto_suggestion_orchestrator.run_once(
        first,
        previews=previews,
        config=config,
        acceptance_blockers=[],
    )
    now_ms["value"] += 1_000
    third, third_status, _runs = auto_suggestion_orchestrator.run_once(
        second,
        previews=previews,
        config=config,
        acceptance_blockers=[],
    )
    now_ms["value"] += 1_000
    fourth, fourth_status, fourth_runs = auto_suggestion_orchestrator.run_once(
        third,
        previews=previews,
        config=config,
        acceptance_blockers=[],
    )
    now_ms["value"] += 1_000
    _fifth, fifth_status, fifth_runs = auto_suggestion_orchestrator.run_once(
        fourth,
        previews=[previews[0]],
        config=config,
        acceptance_blockers=[],
    )

    assert third_status["processed_candidate_ids"] == ["candidate_1", "candidate_2"]
    assert third_status["terminal_failed_candidate_ids"] == ["candidate_1"]
    assert fourth_runs == []
    assert fourth_status["processed_candidate_ids"] == ["candidate_1", "candidate_2"]
    assert fourth_status["last_suppression_reason"] == "state_capacity_exceeded"
    assert fifth_runs == []
    assert fifth_status["last_suppression_reason"] == "duplicate"
    assert calls == ["candidate_1", "candidate_1", "candidate_2"]


def test_active_reservation_capacity_blocks_new_claim_without_eviction(monkeypatch):
    monkeypatch.setattr(auto_suggestion_orchestrator, "MAX_CANDIDATE_RESERVATIONS", 2)
    monkeypatch.setattr(
        auto_suggestion_orchestrator,
        "MAX_CANDIDATE_ATTEMPT_COUNTS",
        2,
    )
    record, previews = _auto_record_with_candidates(at_ms_values=(3_000,))
    record = _apply_runtime_policy(record)
    record["auto_suggestion"].update({
        "candidate_reservations": {
            "active_1": {
                "candidate_id": "active_1",
                "claim_id": "active_claim_1",
                "status": "reserved",
                "reserved_at_ms": 50_000,
                "lease_expires_at_ms": 110_000,
                "attempt_count": 1,
            },
            "active_2": {
                "candidate_id": "active_2",
                "claim_id": "active_claim_2",
                "status": "reserved",
                "reserved_at_ms": 50_000,
                "lease_expires_at_ms": 110_000,
                "attempt_count": 1,
            },
        },
        "candidate_attempt_counts": {"active_1": 1, "active_2": 1},
    })

    updated, status, claim = auto_suggestion_orchestrator.claim_candidate(
        record,
        previews=previews,
        config=llm_service.LlmConfig(
            base_url="https://gw.example",
            api_key="sk-test",
            model="m1",
        ),
        acceptance_blockers=[],
        claim_id="must_not_claim",
        now_ms=70_000,
    )

    assert claim is None
    assert set(status["candidate_reservations"]) == {"active_1", "active_2"}
    assert set(status["candidate_attempt_counts"]) == {"active_1", "active_2"}
    assert status["last_suppression_reason"] == "state_capacity_exceeded"
    assert updated["suggestion_cards"] == []


def test_candidate_merge_union_preserves_processing_order():
    assert auto_suggestion_orchestrator.order_preserving_union(
        ["candidate_2", "candidate_1"],
        ["candidate_1", "candidate_3"],
    ) == ["candidate_2", "candidate_1", "candidate_3"]


def test_repeated_suppression_polling_remains_bounded_and_audited():
    record = {"session_id": "bounded_polling", "auto_suggestion": {}}
    overflow = 25
    total = auto_suggestion_orchestrator.MAX_SUPPRESSED_ENTRIES + overflow

    for index in range(total):
        record, _status = auto_suggestion_orchestrator.suppress(
            record,
            reason="evaluation_window",
            now_ms=index + 1,
            candidate_id=f"candidate_{index}",
        )

    status = auto_suggestion_orchestrator.status_from_record(record)

    assert len(status["suppressed"]) == (
        auto_suggestion_orchestrator.MAX_SUPPRESSED_ENTRIES
    )
    assert status["suppressed"][0]["candidate_id"] == f"candidate_{overflow}"
    assert status["suppressed"][-1]["candidate_id"] == f"candidate_{total - 1}"
    assert status["truncated_counts"]["suppressed"] == overflow
    assert status["truncated_count"] == overflow
    assert status["state_truncated"] is True
