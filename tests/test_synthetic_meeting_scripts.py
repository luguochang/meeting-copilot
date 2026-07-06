import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = REPO_ROOT / "data" / "asr_eval" / "synthetic_meetings" / "scripts"
TOOL_PATH = REPO_ROOT / "tools" / "synthetic_meeting_script_report.py"

REQUIRED_KEYS = {
    "script_id",
    "scenario",
    "language",
    "turns",
    "technical_entities",
    "expected_state_events",
    "expected_gap_candidates",
    "expected_suggestion_cards",
    "baseline_expectations",
    "expected_engineering_card_count_min",
    "expected_engineering_card_count_max",
}


def load_tool_module():
    spec = importlib.util.spec_from_file_location(
        "synthetic_meeting_script_report",
        TOOL_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_scripts():
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(SCRIPT_DIR.glob("*.json"))
    ]


def test_synthetic_meeting_scripts_cover_required_scenarios_and_annotations():
    scripts = load_scripts()

    assert len(scripts) == 5
    scenarios = {script["scenario"] for script in scripts}
    assert scenarios == {
        "api_review",
        "release_review",
        "incident_review",
        "architecture_review",
        "non_engineering_control",
    }

    for script in scripts:
        assert REQUIRED_KEYS.issubset(script)
        assert script["language"] == "zh-CN"
        assert script["turns"]
        assert all("speaker" in turn and "text" in turn for turn in script["turns"])
        assert isinstance(script["expected_state_events"], list)
        assert isinstance(script["expected_gap_candidates"], list)
        assert isinstance(script["expected_suggestion_cards"], list)
        assert set(script["baseline_expectations"]) == {
            "transcript_only_detects_gap",
            "summary_only_detects_within_window",
            "copilot_should_detect_within_window",
        }

    assert any("P99" in script["technical_entities"] for script in scripts)
    assert any("rollback" in script["expected_gap_candidates"] for script in scripts)


def test_expected_suggestion_cards_are_evidence_backed_and_time_bounded():
    scripts = load_scripts()
    engineering_scripts = [
        script for script in scripts if script["scenario"] != "non_engineering_control"
    ]

    assert engineering_scripts
    for script in engineering_scripts:
        assert script["expected_engineering_card_count_min"] >= 1
        assert script["expected_suggestion_cards"]
        for card in script["expected_suggestion_cards"]:
            assert card["gap_type"] in {
                "owner",
                "deadline",
                "rollback",
                "test_verification",
                "metric_monitoring",
            }
            assert card["suggested_question"]
            assert card["evidence_span_required"] is True
            assert card["trigger_window_seconds"]["min"] >= 0
            assert card["trigger_window_seconds"]["max"] <= 30
            assert card["should_show"] is True


def test_non_engineering_control_requires_zero_engineering_cards():
    scripts = load_scripts()
    non_engineering = next(
        script for script in scripts if script["scenario"] == "non_engineering_control"
    )

    assert non_engineering["technical_entities"] == []
    assert non_engineering["expected_gap_candidates"] == []
    assert non_engineering["expected_suggestion_cards"] == []
    assert non_engineering["expected_engineering_card_count_min"] == 0
    assert non_engineering["expected_engineering_card_count_max"] == 0
    assert non_engineering["baseline_expectations"]["copilot_should_detect_within_window"] is False


def test_synthetic_meeting_script_report_is_safe_and_product_oriented():
    tool = load_tool_module()

    report = tool.build_synthetic_meeting_script_report(SCRIPT_DIR)

    assert report["report_mode"] == "synthetic_meeting_script_coverage"
    assert report["coverage_status"] == "passed"
    assert report["script_count"] == 5
    assert report["safe_to_generate_audio_now"] is False
    assert report["safe_to_read_user_audio"] is False
    assert report["safe_to_read_configs_local"] is False
    assert report["safe_to_call_remote_asr"] is False
    assert report["safe_to_call_llm"] is False
    assert report["next_action"] == "create_synthetic_audio_generation_plan"
    assert report["required_product_annotations"] == [
        "expected_state_events",
        "expected_gap_candidates",
        "expected_suggestion_cards",
        "baseline_expectations",
    ]
    report_json = json.dumps(report, ensure_ascii=False)
    assert "/Users/" not in report_json
    assert "configs/local" not in report_json


def test_synthetic_meeting_script_report_rejects_forbidden_or_absolute_script_dirs():
    tool = load_tool_module()

    for script_dir in [
        Path("configs/local"),
        Path("data/asr_eval/local_samples"),
        Path("/tmp/synthetic_meetings"),
        Path("data/asr_eval/synthetic_meetings/../local_samples"),
    ]:
        report = tool.build_synthetic_meeting_script_report(script_dir)
        assert report["coverage_status"] == "failed"
        assert "script_dir is not allowed" in report["coverage_errors"]
        assert report["safe_to_read_configs_local"] is False
