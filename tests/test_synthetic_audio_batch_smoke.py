import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "synthetic_audio_batch_smoke.py"


def load_tool_module():
    spec = importlib.util.spec_from_file_location(
        "synthetic_audio_batch_smoke",
        TOOL_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_batch_smoke_dry_run_covers_all_synthetic_scripts_without_execution():
    tool = load_tool_module()

    report = tool.build_synthetic_audio_batch_smoke_report(
        target_root="artifacts/tmp/synthetic_audio",
        execute_local_tts=False,
    )

    assert report["report_mode"] == "synthetic_audio_batch_smoke"
    assert report["report_version"] == "synthetic_audio_batch_smoke.v1"
    assert report["batch_status"] == "ready_for_explicit_local_tts_execution"
    assert report["script_count"] == 5
    assert report["script_ids"] == [
        "api-review-001",
        "architecture-review-001",
        "incident-review-001",
        "non-engineering-control-001",
        "release-review-001",
    ]
    assert report["target_root"] == "artifacts/tmp/synthetic_audio"
    assert report["safe_to_execute_local_tts_now"] is False
    assert report["safe_to_call_remote_tts"] is False
    assert report["safe_to_read_user_audio"] is False
    assert report["safe_to_read_configs_local"] is False
    assert report["safe_to_call_remote_asr"] is False
    assert report["safe_to_call_llm"] is False
    assert report["safe_to_commit_generated_audio"] is False
    assert report["validation_errors"] == []
    assert {item["generation_status"] for item in report["items"]} == {"not_started"}
    report_json = json.dumps(report, ensure_ascii=False)
    assert "/Users/" not in report_json
    assert "configs/local" not in report_json


def test_batch_smoke_execution_harness_can_skip_commands_but_proves_batch_wiring():
    tool = load_tool_module()

    report = tool.build_synthetic_audio_batch_smoke_report(
        target_root="artifacts/tmp/synthetic_audio",
        execute_local_tts=True,
        run_commands=False,
    )

    assert report["batch_status"] == "ready_for_local_tts_execution"
    assert report["safe_to_execute_local_tts_now"] is True
    assert {item["generation_status"] for item in report["items"]} == {
        "execution_skipped_by_test_harness"
    }
    assert report["next_action"] == "execute_local_tts_or_run_local_asr_event_plan"


def test_batch_smoke_blocks_forbidden_target_root():
    tool = load_tool_module()

    report = tool.build_synthetic_audio_batch_smoke_report(
        target_root="data/asr_eval/local_samples",
        execute_local_tts=False,
    )

    assert report["batch_status"] == "blocked"
    assert report["safe_to_execute_local_tts_now"] is False
    assert "target_root is not allowed" in report["validation_errors"]
    assert "target_root is forbidden" in report["validation_errors"]
