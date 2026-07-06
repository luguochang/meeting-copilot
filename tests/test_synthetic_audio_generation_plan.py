import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "synthetic_audio_generation_plan.py"


def load_tool_module():
    spec = importlib.util.spec_from_file_location(
        "synthetic_audio_generation_plan",
        TOOL_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_synthetic_audio_plan_is_local_only_and_no_generation_by_default():
    tool = load_tool_module()

    report = tool.build_synthetic_audio_generation_plan(
        script_id="api-review-001",
        tts_engine="macos_say",
        target_root="artifacts/tmp/synthetic_audio",
        max_duration_seconds=240,
    )

    assert report["plan_mode"] == "synthetic_audio_generation_plan_only"
    assert report["plan_version"] == "synthetic_audio_generation_plan.v1"
    assert report["plan_status"] == "ready_for_manual_generation_review"
    assert report["generation_status"] == "not_started"
    assert report["safe_to_generate_audio_now"] is False
    assert report["safe_to_read_user_audio"] is False
    assert report["safe_to_read_configs_local"] is False
    assert report["safe_to_call_remote_tts"] is False
    assert report["safe_to_call_remote_asr"] is False
    assert report["safe_to_call_llm"] is False
    assert report["safe_to_commit_generated_audio"] is False
    assert report["script_id"] == "api-review-001"
    assert report["tts_engine"] == "macos_say"
    assert report["target_root"] == "artifacts/tmp/synthetic_audio"
    assert report["max_duration_seconds"] == 240
    assert report["next_action"] == "manual_local_tts_smoke"
    assert report["validation_errors"] == []


def test_synthetic_audio_plan_rejects_remote_tts():
    tool = load_tool_module()

    report = tool.build_synthetic_audio_generation_plan(
        script_id="api-review-001",
        tts_engine="remote_tts",
        target_root="artifacts/tmp/synthetic_audio",
        max_duration_seconds=240,
    )

    assert report["plan_status"] == "blocked"
    assert "tts_engine is not an approved local engine" in report["validation_errors"]
    assert report["safe_to_call_remote_tts"] is False


def test_synthetic_audio_plan_rejects_forbidden_roots_and_unbounded_duration():
    tool = load_tool_module()

    report = tool.build_synthetic_audio_generation_plan(
        script_id="api-review-001",
        tts_engine="macos_say",
        target_root="data/asr_eval/local_samples",
        max_duration_seconds=0,
    )

    assert report["plan_status"] == "blocked"
    assert "target_root is not allowed" in report["validation_errors"]
    assert "max_duration_seconds must be between 1 and 1800" in report["validation_errors"]
    assert report["safe_to_generate_audio_now"] is False


def test_synthetic_audio_plan_rejects_unknown_or_unsafe_script_ids():
    tool = load_tool_module()

    for script_id in ["unknown-script", "../api-review-001", "api review 001"]:
        report = tool.build_synthetic_audio_generation_plan(
            script_id=script_id,
            tts_engine="macos_say",
            target_root="artifacts/tmp/synthetic_audio",
            max_duration_seconds=240,
        )
        assert report["plan_status"] == "blocked"
        assert "script_id is not approved" in report["validation_errors"]
        assert report["safe_to_read_configs_local"] is False
