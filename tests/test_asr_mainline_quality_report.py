import importlib.util
import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "asr_mainline_quality_report.py"


def load_tool_module():
    spec = importlib.util.spec_from_file_location("asr_mainline_quality_report", TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_asr_mainline_quality_report_separates_pipeline_closure_from_asr_quality(tmp_path):
    tool = load_tool_module()
    reference = tmp_path / "reference.txt"
    annotation = tmp_path / "annotation.json"
    transcript = tmp_path / "funasr.transcript-report.json"
    replay = tmp_path / "funasr.live-pipeline-replay.json"
    output = tmp_path / "report.json"

    reference.write_text(
        "payment-gateway 先灰度 10%，如果 P99 超过 800ms 或错误率超过 0.1% 就回滚。李四补充 Grafana 看板。",
        encoding="utf-8",
    )
    write_json(
        annotation,
        {
            "technical_entities": [
                {"normalized": "payment-gateway"},
                {"normalized": "10%"},
                {"normalized": "P99"},
                {"normalized": "800ms"},
                {"normalized": "错误率"},
                {"normalized": "0.1%"},
                {"normalized": "回滚"},
                {"normalized": "Grafana"},
            ],
        },
    )
    write_json(
        transcript,
        {
            "provider": "funasr",
            "normalized_text": "checkout-service 先灰度 10% 先看 error_rate P99 如果指标异常暂停扩量但回滚脚本还没有在",
            "duration_seconds": 11.2,
            "latency_ms": 11120,
            "rtf": 0.99,
            "segments": [{"id": "s1"}, {"id": "s2"}],
        },
    )
    write_json(
        replay,
        {
            "replay_status": "asr_events_replayed_to_live_pipeline",
            "short_local_simulated_input_status": "closed_to_candidate_timeline",
            "evidence_span_count": 4,
            "state_event_count": 2,
            "suggestion_candidate_count": 2,
            "llm_request_draft_count": 2,
            "all_llm_statuses": ["not_called"],
            "asr_metrics": {
                "first_partial_latency_ms": 3733,
                "first_final_latency_ms": 5358,
                "stream_duration_ms": 11297,
            },
            "safe_to_call_llm_now": False,
            "safe_to_call_remote_asr_now": False,
        },
    )

    report = tool.build_asr_mainline_quality_report(
        sample_id="release-review-test",
        reference_path=reference,
        annotation_path=annotation,
        provider_reports=[
            {
                "provider": "funasr_streaming",
                "transcript_report_path": transcript,
                "pipeline_replay_report_path": replay,
            }
        ],
        output_path=output,
    )

    assert output.exists()
    assert report["schema_version"] == "asr_mainline_quality_report.v1"
    assert report["sample_id"] == "release-review-test"
    assert report["privacy_cost_flags"] == {
        "remote_asr_called": False,
        "llm_called": False,
        "raw_audio_uploaded": False,
        "user_audio_committed_to_repo": False,
    }
    assert report["summary"]["provider_count"] == 1
    assert report["summary"]["pipeline_closed_count"] == 1
    assert report["summary"]["quality_pass_count"] == 0
    assert report["default_decision"]["decision_status"] == "no_go_quality_not_production"
    assert "pipeline_closed_but_asr_quality_insufficient" in report["default_decision"]["blockers"]

    candidate = report["providers"][0]
    assert candidate["provider"] == "funasr_streaming"
    assert candidate["pipeline"]["closed_to_candidate_timeline"] is True
    assert candidate["pipeline"]["llm_called"] is False
    assert candidate["quality"]["term_recall"] == 0.5
    assert candidate["quality"]["missing_terms"] == ["0.1%", "800ms", "Grafana", "payment-gateway"]
    assert candidate["quality"]["passed_minimum_quality_gate"] is False
    assert candidate["quality"]["char_error_rate"] > 0


def test_asr_mainline_quality_report_cli_writes_machine_readable_report(tmp_path):
    reference = tmp_path / "reference.txt"
    annotation = tmp_path / "annotation.json"
    transcript = tmp_path / "sherpa.transcript-report.json"
    replay = tmp_path / "sherpa.live-pipeline-replay.json"
    output = tmp_path / "report.json"

    reference.write_text("接口先灰度，P99 超过 800ms 就回滚。", encoding="utf-8")
    write_json(annotation, {"technical_entities": [{"normalized": "P99"}, {"normalized": "800ms"}, {"normalized": "回滚"}]})
    write_json(transcript, {"provider": "sherpa", "normalized_text": "接口先灰度 P99 超过 800ms 就回滚", "segments": [{"id": "s1"}]})
    write_json(
        replay,
        {
            "replay_status": "asr_events_replayed_to_live_pipeline",
            "short_local_simulated_input_status": "closed_to_candidate_timeline",
            "evidence_span_count": 1,
            "suggestion_candidate_count": 1,
            "all_llm_statuses": ["not_called"],
            "safe_to_call_llm_now": False,
            "safe_to_call_remote_asr_now": False,
        },
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(TOOL_PATH),
            "--sample-id",
            "cli-smoke",
            "--reference",
            str(reference),
            "--annotation",
            str(annotation),
            "--provider",
            "sherpa",
            "--transcript-report",
            str(transcript),
            "--pipeline-replay-report",
            str(replay),
            "--output",
            str(output),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "asr_mainline_quality_report.v1"
    assert payload["default_decision"]["decision_status"] == "candidate_for_next_real_audio_gate"
    assert "quality_pass_count" in completed.stdout


def test_quality_report_counts_visible_release_near_miss_aliases_without_staging_backfill():
    tool = load_tool_module()

    quality = tool._quality(
        reference_text=(
            "这次 checkout-service 周五晚上灰度百分之十，先看 error_rate 和 P99。"
            "如果指标异常我们暂停扩量，但回滚脚本还没有在 staging 跑过。"
        ),
        hypothesis="这次 check outservice 周五晚上灰度 10% 先看 error r ate 和 P99 回滚脚本还没有在 ing 跑过",
        expected_terms=["checkout-service", "error_rate", "P99", "staging"],
    )

    assert quality["matched_terms"] == ["P99", "checkout-service", "error_rate"]
    assert quality["missing_terms"] == ["staging"]
    assert quality["term_recall"] == 0.75
