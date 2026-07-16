from fastapi.testclient import TestClient

from meeting_copilot_web_mvp import llm_service
from meeting_copilot_web_mvp.app import create_app
from meeting_copilot_web_mvp.sqlite_repository import SqliteAsrLiveSessionRepository


def _create_mock_session(
    client: TestClient,
    session_id: str = "packaged_demo_no_cost",
    text: str = "先灰度 5%。谁负责回滚？如果错误率超过 0.1% 就回滚。",
) -> None:
    response = client.post(
        "/live/asr/mock/sessions",
        json={
            "session_id": session_id,
            "provider": "local_mock_asr",
            "streaming_events": [
                {
                    "event_type": "final",
                    "segment_id": "s1",
                    "text": text,
                    "start_ms": 0,
                    "end_ms": 3200,
                    "received_at_ms": 3500,
                    "confidence": 0.91,
                }
            ],
        },
    )
    assert response.status_code == 201, response.text


def _create_semantic_blocked_real_session(tmp_path, session_id: str = "semantic_blocked_real_mic") -> TestClient:
    client = TestClient(create_app(data_dir=tmp_path))
    repo = SqliteAsrLiveSessionRepository(tmp_path)
    repo.create(
        {
            "session_id": session_id,
            "source": "live_asr_stream",
            "trace_kind": "live_event",
            "provider": "sherpa_onnx_realtime",
            "provider_mode": "real",
            "is_mock": False,
            "input_source": "browser_live_mic",
            "audio_source": "browser_live_mic",
            "asr_fallback_used": False,
            "degradation_reasons": ["asr_semantic_quality_blocked"],
            "asr_semantic_quality": {
                "schema_version": "asr_semantic_quality.v1",
                "status": "blocked",
                "blocker": "asr_semantic_quality_blocked",
                "reason": "technical_entity_threshold_not_met",
                "matched_entities": ["今天"],
                "matched_entity_groups": ["deadline"],
                "missing_entity_groups": ["release_control", "reliability", "ownership", "action"],
                "technical_entity_hit_count": 1,
                "technical_group_hit_count": 1,
                "gibberish_score": 0,
            },
            "events": [
                {
                    "event_type": "transcript_final",
                    "id": "transcript_final:noise",
                    "at_ms": 300000,
                    "source": "live_asr_stream",
                    "trace_kind": "live_event",
                    "sequence": 1,
                    "payload": {
                        "segment_id": "noise",
                        "text": "request到contest xt moden downtwo calling to methoc ine ofdel背熟midiwell。",
                        "normalized_text": "request到contest xt moden downtwo calling to methoc ine ofdel背熟midiwell。",
                        "start_ms": 0,
                        "end_ms": 300000,
                        "confidence": 0.8,
                        "is_final": True,
                    },
                }
            ],
        }
    )
    return client


def test_demo_no_cost_derivation_generates_cards_minutes_without_llm_gateway(monkeypatch):
    monkeypatch.delenv("LLM_GATEWAY_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_GATEWAY_API_KEY", raising=False)
    monkeypatch.setattr(llm_service, "REPO_ENV_FILE", "missing.env")
    client = TestClient(create_app())
    _create_mock_session(client)

    suggestions = client.post(
        "/live/asr/demo/sessions/packaged_demo_no_cost/llm-execution-runs",
        json={"mode": "deterministic_demo"},
    )
    approach = client.post(
        "/live/asr/demo/sessions/packaged_demo_no_cost/approach-cards",
        json={"mode": "deterministic_demo"},
    )
    minutes = client.post(
        "/live/asr/demo/sessions/packaged_demo_no_cost/minutes",
        json={"mode": "deterministic_demo"},
    )
    snapshot = client.get("/live/asr/sessions/packaged_demo_no_cost/events")
    history = client.get("/live/asr/sessions?include_demo=true")

    assert suggestions.status_code == 200, suggestions.text
    assert approach.status_code == 200, approach.text
    assert minutes.status_code == 200, minutes.text
    assert snapshot.status_code == 200

    suggestion_body = suggestions.json()
    assert suggestion_body["executor_mode"] == "deterministic_demo"
    assert suggestion_body["execution_boundary"] == "demo_no_cost_execution"
    assert suggestion_body["llm_provider"]["provider"] == "deterministic_demo"
    assert suggestion_body["llm_provider"]["is_mock"] is True
    assert suggestion_body["run_count"] >= 1
    assert suggestion_body["runs"][0]["llm_call_status"] == "not_called"
    assert suggestion_body["runs"][0]["cost_status"] == "no_cost"
    assert suggestion_body["runs"][0]["card"]["suggestion_text"]

    approach_body = approach.json()
    assert approach_body["execution_boundary"] == "demo_no_cost_execution"
    assert approach_body["count"] >= 1
    assert approach_body["llm_provider"]["provider"] == "deterministic_demo"
    assert approach_body["llm_usage"]["total_tokens"] == 0

    minutes_body = minutes.json()
    assert minutes_body["execution_boundary"] == "demo_no_cost_execution"
    assert "# 会议纪要" in minutes_body["minutes_md"]
    assert minutes_body["llm_usage"]["total_tokens"] == 0

    snapshot_body = snapshot.json()
    assert len(snapshot_body["suggestion_cards"]) >= 1
    assert len(snapshot_body["approach_cards"]) >= 1
    assert "# 会议纪要" in snapshot_body["minutes"]["minutes_md"]

    indexed = {item["session_id"]: item for item in history.json()["sessions"]}
    assert indexed["packaged_demo_no_cost"]["suggestion_card_count"] >= 1
    assert indexed["packaged_demo_no_cost"]["approach_card_count"] >= 1
    assert indexed["packaged_demo_no_cost"]["has_minutes"] is True


def test_demo_no_cost_derivation_uses_transcript_context_for_ordinary_meeting(monkeypatch):
    monkeypatch.delenv("LLM_GATEWAY_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_GATEWAY_API_KEY", raising=False)
    monkeypatch.setattr(llm_service, "REPO_ENV_FILE", "missing.env")
    client = TestClient(create_app())
    _create_mock_session(
        client,
        "ordinary_user_feedback_no_cost",
        (
            "今天我们先讨论用户访谈反馈。第一个问题是新用户不知道从哪里开始，"
            "所以首页需要更清楚地告诉他下一步该做什么。第二个问题是会议过程中"
            "信息会越来越多，大家希望页面不要只显示最后一句话，而是能够持续看到"
            "前面的讨论内容。第三个问题是导出记录要简单，最好结束后马上能看到录音、"
            "文字稿和会议纪要。王五负责整理反馈，李四明天确认页面文案，张三补充测试清单。"
        ),
    )

    suggestions = client.post(
        "/live/asr/demo/sessions/ordinary_user_feedback_no_cost/llm-execution-runs",
        json={"mode": "deterministic_demo"},
    )
    approach = client.post(
        "/live/asr/demo/sessions/ordinary_user_feedback_no_cost/approach-cards",
        json={"mode": "deterministic_demo"},
    )
    minutes = client.post(
        "/live/asr/demo/sessions/ordinary_user_feedback_no_cost/minutes",
        json={"mode": "deterministic_demo"},
    )

    assert suggestions.status_code == 200, suggestions.text
    assert approach.status_code == 200, approach.text
    assert minutes.status_code == 200, minutes.text

    suggestion_text = suggestions.json()["runs"][0]["card"]["suggestion_text"]
    approach_text = approach.json()["approach_cards"][0]["suggestion_text"]
    minutes_md = minutes.json()["minutes_md"]
    combined = "\n".join([suggestion_text, approach_text, minutes_md])

    assert "用户访谈反馈" in combined
    assert "首页" in combined
    assert "持续看到" in combined
    assert "录音、文字稿和会议纪要" in combined
    assert "王五负责整理反馈" in combined
    for unrelated in ["灰度", "回滚", "P99", "错误率"]:
        assert unrelated not in combined


def test_demo_no_cost_derivation_preserves_mixed_product_and_release_context(monkeypatch):
    monkeypatch.delenv("LLM_GATEWAY_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_GATEWAY_API_KEY", raising=False)
    monkeypatch.setattr(llm_service, "REPO_ENV_FILE", "missing.env")
    client = TestClient(create_app())
    _create_mock_session(
        client,
        "mixed_product_release_no_cost",
            (
                "第一部分我们讨论用户访谈反馈。新用户不知道从哪里开始，所以首页需要更清楚地告诉他下一步该做什么。"
                "会议过程中信息会越来越多，页面不要只显示最后一句话，要持续看到前面的讨论内容。"
                "导出记录要简单，结束后马上能看到录音、文字稿和会议纪要。"
                "第二部分我们讨论发布评审。payment gateway 先灰度百分之五，如果 P99 延迟超过九百毫秒，"
                "或者错误率超过百分之零点一，就要立即回滚。张三负责今天补 SLO 看板。"
            ),
    )

    suggestions = client.post(
        "/live/asr/demo/sessions/mixed_product_release_no_cost/llm-execution-runs",
        json={"mode": "deterministic_demo"},
    )
    approach = client.post(
        "/live/asr/demo/sessions/mixed_product_release_no_cost/approach-cards",
        json={"mode": "deterministic_demo"},
    )
    minutes = client.post(
        "/live/asr/demo/sessions/mixed_product_release_no_cost/minutes",
        json={"mode": "deterministic_demo"},
    )

    assert suggestions.status_code == 200, suggestions.text
    assert approach.status_code == 200, approach.text
    assert minutes.status_code == 200, minutes.text

    suggestion_text = suggestions.json()["runs"][0]["card"]["suggestion_text"]
    approach_text = approach.json()["approach_cards"][0]["suggestion_text"]
    minutes_md = minutes.json()["minutes_md"]

    assert "用户访谈反馈" in suggestion_text
    assert "发布评审" in suggestion_text
    assert "首页引导" in approach_text
    assert "发布门禁" in approach_text
    assert "首页需要更清楚地告诉新用户下一步该做什么" in minutes_md
    assert "会议中需要持续看到前面的讨论内容" in minutes_md
    assert "先按小流量灰度推进" in minutes_md
    assert "错误率阈值作为回滚条件" in minutes_md


def test_no_cost_derivation_suppresses_formal_outputs_for_semantic_blocked_real_session(monkeypatch, tmp_path):
    monkeypatch.delenv("LLM_GATEWAY_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_GATEWAY_API_KEY", raising=False)
    monkeypatch.setattr(llm_service, "REPO_ENV_FILE", "missing.env")
    client = _create_semantic_blocked_real_session(tmp_path)

    suggestions = client.post(
        "/live/asr/demo/sessions/semantic_blocked_real_mic/llm-execution-runs",
        json={"mode": "deterministic_demo"},
    )
    approach = client.post(
        "/live/asr/demo/sessions/semantic_blocked_real_mic/approach-cards",
        json={"mode": "deterministic_demo"},
    )
    minutes = client.post(
        "/live/asr/demo/sessions/semantic_blocked_real_mic/minutes",
        json={"mode": "deterministic_demo"},
    )
    snapshot = client.get("/live/asr/sessions/semantic_blocked_real_mic/events")

    assert suggestions.status_code == 200, suggestions.text
    assert approach.status_code == 200, approach.text
    assert minutes.status_code == 200, minutes.text

    suggestion_body = suggestions.json()
    assert suggestion_body["execution_boundary"] == "demo_no_cost_quality_blocked"
    assert suggestion_body["derivation_blocked"] is True
    assert suggestion_body["run_count"] == 0
    assert suggestion_body["runs"] == []
    assert "asr_semantic_quality_blocked" in suggestion_body["acceptance_blockers"]

    approach_body = approach.json()
    assert approach_body["execution_boundary"] == "demo_no_cost_quality_blocked"
    assert approach_body["degraded"] is True
    assert approach_body["derivation_blocked"] is True
    assert approach_body["approach_cards"] == []
    assert approach_body["count"] == 0

    minutes_body = minutes.json()
    assert minutes_body["execution_boundary"] == "demo_no_cost_quality_blocked"
    assert minutes_body["degraded"] is True
    assert minutes_body["derivation_blocked"] is True
    assert minutes_body["minutes_md"] == ""

    snapshot_body = snapshot.json()
    assert snapshot_body["suggestion_cards"] == []
    assert snapshot_body["approach_cards"] == []
    assert snapshot_body["minutes"] == {}


def test_production_endpoints_reject_deterministic_demo_mode(monkeypatch):
    monkeypatch.delenv("LLM_GATEWAY_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_GATEWAY_API_KEY", raising=False)
    monkeypatch.setattr(llm_service, "REPO_ENV_FILE", "missing.env")
    client = TestClient(create_app())
    _create_mock_session(client, "production_rejects_demo_mode")

    suggestions = client.post(
        "/live/asr/sessions/production_rejects_demo_mode/llm-execution-runs",
        json={"mode": "deterministic_demo"},
    )
    approach = client.post(
        "/live/asr/sessions/production_rejects_demo_mode/approach-cards",
        json={"mode": "deterministic_demo"},
    )
    minutes = client.post(
        "/live/asr/sessions/production_rejects_demo_mode/minutes",
        json={"mode": "deterministic_demo"},
    )

    assert suggestions.status_code == 422
    assert approach.status_code == 422
    assert minutes.status_code == 422
