from __future__ import annotations

from io import BytesIO
import json
from zipfile import ZipFile

from fastapi.testclient import TestClient

from meeting_copilot_web_mvp.app import create_app


def test_diagnostic_bundle_endpoint_downloads_allowlist_only_runtime_evidence(
    tmp_path,
    monkeypatch,
):
    secret = "sk-next007-api-secret"
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gateway.example/v1?token=private")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", secret)
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "test-model")

    with TestClient(create_app(data_dir=tmp_path)) as client:
        response = client.get("/v2/diagnostics/bundle")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["content-disposition"] == (
        'attachment; filename="meeting-copilot-diagnostics.zip"'
    )
    assert secret.encode() not in response.content
    assert b"gateway.example" not in response.content
    assert str(tmp_path).encode() not in response.content

    with ZipFile(BytesIO(response.content)) as archive:
        assert archive.namelist() == ["diagnostics.json", "manifest.json"]
        diagnostics = json.loads(archive.read("diagnostics.json"))
        manifest = json.loads(archive.read("manifest.json"))

    assert diagnostics["schema_version"] == "meeting_copilot.diagnostic_bundle.v1"
    assert diagnostics["config_summary"]["network_mode"] == "local_audio_remote_llm_only"
    assert diagnostics["config_summary"]["language"] == "zh-CN"
    assert diagnostics["provider_capabilities"]
    assert manifest["privacy"]["freeform_meeting_content_included"] is False
    assert manifest["privacy"]["secret_values_included"] is False


def test_realtime_slo_api_and_diagnostic_bundle_use_content_free_aggregates(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("LLM_PROMPT_CNY_PER_1M_TOKENS", "10")
    monkeypatch.setenv("LLM_COMPLETION_CNY_PER_1M_TOKENS", "20")
    app = create_app(data_dir=tmp_path)
    meeting_id = "meeting-slo-api"
    app.state.v2_persistence.create_meeting(
        meeting_id=meeting_id,
        title="不得进入诊断包的标题",
        now_ms=1_000,
    )
    stages = {
        "final_committed": 100,
        "job_queued": 120,
        "job_claimed": 180,
        "provider_connected": 200,
        "first_token": 350,
        "provider_completed": 700,
        "event_emitted": 750,
        "ui_rendered": 800,
    }
    for stage, milliseconds in stages.items():
        app.state.pipeline_traces.record(
            "job-slo-api",
            stage,
            meeting_id=meeting_id,
            job_id="job-slo-api",
            monotonic_ns=milliseconds * 1_000_000,
            attributes={"lane": "correction"} if stage == "job_queued" else None,
        )
    app.state.settings_usage_repository.record_usage(
        session_id=meeting_id,
        purpose="realtime_intelligence",
        provider="openai_compatible",
        model="fast-local-test",
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
        timestamp_ms=2_000,
    )

    with TestClient(app) as client:
        meeting_report = client.get(f"/v2/meetings/{meeting_id}/realtime-ai-slo")
        all_reports = client.get("/v2/diagnostics/realtime-ai-slo")
        bundle = client.get("/v2/diagnostics/bundle")

    assert meeting_report.status_code == 200
    correction = meeting_report.json()["lanes"]["correction"]
    assert correction["metrics"]["provider_ttft_ms"]["p95_ms"] == 150.0
    assert correction["metrics"]["final_to_event_emitted_ms"]["p95_ms"] == 650.0
    assert meeting_report.json()["token_usage"] == {
        "call_count": 1,
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "total_tokens": 150,
        "estimated_cost_cny": 0.002,
        "cost_status": "estimated",
        "purposes": {
            "realtime_intelligence": {
                "call_count": 1,
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
            }
        },
    }
    assert all_reports.json()["meetings"][meeting_id]["trace_count"] == 1
    assert (tmp_path / "diagnostics" / "realtime-ai-slo.json").is_file()

    with ZipFile(BytesIO(bundle.content)) as archive:
        diagnostics = json.loads(archive.read("diagnostics.json"))
    slo_metrics = next(
        item
        for item in diagnostics["stage_metrics"]
        if item["stage"] == "realtime_ai_correction"
    )["metrics"]
    assert slo_metrics["sample_count"] == 1
    assert slo_metrics["provider_ttft_p95_ms"] == 150.0
    assert slo_metrics["final_to_event_p95_ms"] == 650.0
    serialized = json.dumps(diagnostics, ensure_ascii=False)
    assert meeting_id not in serialized
    assert "不得进入诊断包的标题" not in serialized
