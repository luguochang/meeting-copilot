import importlib.util
import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "long_meeting_soak_runner.py"


def load_tool_module():
    spec = importlib.util.spec_from_file_location(
        "long_meeting_soak_runner",
        TOOL_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fake_go_metrics(**overrides):
    metrics = {
        "asr_elapsed_seconds": 120.0,
        "llm_call_count": 8,
        "llm_usage_total_tokens": 2400,
        "rss_mb_start": 128.0,
        "rss_mb_peak": 152.0,
        "rss_mb_end": 148.0,
        "card_count": 8,
        "remote_asr_called": False,
        "llm_called": False,
        "events": [
            {"time_seconds": 60, "type": "card"},
            {"time_seconds": 180, "type": "card"},
            {"time_seconds": 330, "type": "card"},
            {"time_seconds": 510, "type": "card"},
            {"time_seconds": 720, "type": "card"},
            {"time_seconds": 900, "type": "card"},
            {"time_seconds": 1050, "type": "card"},
            {"time_seconds": 1170, "type": "card"},
        ],
    }
    metrics.update(overrides)
    return metrics


def test_soak_runner_writes_go_report_for_20_minute_simulated_plan(tmp_path):
    tool = load_tool_module()

    result = tool.run_long_meeting_soak(
        run_id="unit-go",
        artifact_root=tmp_path / "soak",
        duration_minutes=20,
        metrics=fake_go_metrics(),
    )

    report = result["report"]
    assert report["schema_version"] == "long_meeting_soak_report.v1"
    assert report["duration_minutes"] == 20
    assert report["expected_audio_seconds"] == 1200
    assert report["chunk_count"] == 600
    assert report["asr_rtf"] == 0.1
    assert report["llm_call_count"] == 8
    assert report["llm_usage_total_tokens"] == 2400
    assert report["memory_rss"]["status"] == "available"
    assert report["card_count"] == 8
    assert report["suppression_count"] == 0
    assert report["privacy_cost_flags"]["remote_asr_called"] is False
    assert report["privacy_cost_flags"]["llm_called"] is False
    assert report["privacy_cost_flags"]["secret_leaked"] is False
    assert report["verdict"] == "go"
    assert report["blockers"] == []
    assert len(report["input_plan"]["chunks"]) == 600

    report_path = Path(result["artifact_root"]) / "soak_report.json"
    assert report_path.exists()
    persisted = json.loads(report_path.read_text(encoding="utf-8"))
    assert persisted == report


def test_soak_runner_suppresses_or_blocks_when_cards_exceed_frequency_cap(tmp_path):
    tool = load_tool_module()

    result = tool.run_long_meeting_soak(
        run_id="unit-frequency",
        artifact_root=tmp_path / "soak",
        duration_minutes=20,
        metrics=fake_go_metrics(
            card_count=24,
            events=[{"time_seconds": second, "type": "card"} for second in range(0, 1200, 50)],
        ),
        max_cards_per_10_minutes=6,
    )

    report = result["report"]
    assert report["verdict"] == "no_go"
    assert report["suppression_count"] == 12
    assert "suggestion_frequency_cap_exceeded" in report["blockers"]
    assert report["privacy_cost_flags"]["suggestion_frequency_capped"] is True


def test_soak_runner_blocks_missing_or_invalid_metrics(tmp_path):
    tool = load_tool_module()

    result = tool.run_long_meeting_soak(
        run_id="unit-missing-metrics",
        artifact_root=tmp_path / "soak",
        duration_minutes=20,
        metrics={
            "llm_call_count": -1,
            "llm_usage_total_tokens": 0,
            "remote_asr_called": False,
            "llm_called": False,
        },
    )

    report = result["report"]
    assert report["verdict"] == "blocked"
    assert "metric_missing:asr_elapsed_seconds" in report["blockers"]
    assert "metric_invalid:llm_call_count" in report["blockers"]
    assert report["memory_rss"]["status"] == "unavailable"


def test_soak_runner_report_json_sanitizes_secret_like_values(tmp_path):
    tool = load_tool_module()

    result = tool.run_long_meeting_soak(
        run_id="unit-secret-sanitize",
        artifact_root=tmp_path / "soak",
        duration_minutes=20,
        metrics=fake_go_metrics(
            diagnostic_note="OPENAI_API_KEY=sk-test-secret-value should not persist",
            nested={"authorization": "Bearer abcdef1234567890"},
        ),
    )

    report_path = Path(result["artifact_root"]) / "soak_report.json"
    raw_json = report_path.read_text(encoding="utf-8")
    assert "sk-test-secret-value" not in raw_json
    assert "Bearer abcdef1234567890" not in raw_json
    assert report_path.name == "soak_report.json"
    assert result["report"]["privacy_cost_flags"]["secret_leaked"] is False


def test_soak_runner_cli_writes_report_to_default_soak_artifact_shape(tmp_path):
    completed = subprocess.run(
        [
            sys.executable,
            str(TOOL_PATH),
            "--run-id",
            "cli-unit",
            "--artifact-root",
            str(tmp_path / "soak"),
            "--duration-minutes",
            "20",
            "--fake-metrics-json",
            json.dumps(fake_go_metrics()),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    report_path = tmp_path / "soak" / "cli-unit" / "soak_report.json"
    assert report_path.exists()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["verdict"] == "go"
    assert report["expected_audio_seconds"] == 1200
