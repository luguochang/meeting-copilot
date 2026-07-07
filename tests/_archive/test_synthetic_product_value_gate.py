import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "synthetic_product_value_gate.py"
ALLOWED_REPORT_ROOT = REPO_ROOT / "artifacts" / "tmp" / "asr_reports"
API_SCRIPT_PATH = REPO_ROOT / "data" / "asr_eval" / "synthetic_meetings" / "scripts" / "api-review.json"
NON_ENGINEERING_SCRIPT_PATH = (
    REPO_ROOT
    / "data"
    / "asr_eval"
    / "synthetic_meetings"
    / "scripts"
    / "non-engineering-control.json"
)


def load_tool_module():
    spec = importlib.util.spec_from_file_location(
        "synthetic_product_value_gate",
        TOOL_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_allowed_smoke_report(name: str, payload: dict[str, object]) -> Path:
    ALLOWED_REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    path = ALLOWED_REPORT_ROOT / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _smoke_report(*, recall, final_count=1, eos_count=1, error_count=0):
    return {
        "report_mode": "synthetic_asr_smoke_report",
        "report_version": "synthetic_asr_smoke_report.v1",
        "status": "completed",
        "script_id": "api-review-001",
        "normalized_technical_entity_recall": recall,
        "normalized_technical_entity_precision": recall,
        "technical_entity_count": 4,
        "normalized_missing_entities": ["payment-gateway"] if recall < 1.0 else [],
        "event_counts": {
            "partial": 2,
            "final": final_count,
            "revision": 0,
            "error": error_count,
            "end_of_stream": eos_count,
        },
        "quality_gate": {
            "passes_first_pilot_entity_threshold": recall >= 0.8,
            "passes_product_entity_target": recall >= 0.9,
            "has_final_event": final_count > 0,
            "has_end_of_stream": eos_count == 1,
        },
    }


def test_product_value_gate_blocks_engineering_script_when_asr_entities_are_too_weak():
    smoke_path = _write_allowed_smoke_report(
        "test-api-review-weak.synthetic-product-gate.smoke-report.json",
        _smoke_report(recall=0.5),
    )
    tool = load_tool_module()

    report = tool.build_synthetic_product_value_gate_report(
        smoke_report_path=smoke_path,
        script_json_path=API_SCRIPT_PATH,
    )

    assert report["report_mode"] == "synthetic_product_value_gate"
    assert report["report_version"] == "synthetic_product_value_gate.v1"
    assert report["status"] == "completed"
    assert report["script_id"] == "api-review-001"
    assert report["product_value_decision"] == "needs_asr_quality_work"
    assert report["ready_for_desktop_runtime_validation"] is False
    assert report["ready_for_real_mic_pilot"] is False
    assert report["expected_engineering_card_count_range"] == {"min": 1, "max": 3}
    assert report["expected_gap_candidates"] == ["owner", "rollback", "metric_monitoring"]
    assert report["expected_card_count"] == 2
    assert report["gate_failures"] == [
        "normalized technical entity recall below first-pilot threshold"
    ]
    assert report["next_action"] == "improve_local_asr_quality_or_prepare_model_approval"
    assert report["safe_to_call_llm"] is False
    assert report["safe_to_call_remote_asr"] is False
    assert report["safe_to_read_user_audio"] is False
    assert report["safe_to_read_configs_local"] is False


def test_product_value_gate_allows_desktop_runtime_when_engineering_asr_gate_is_met():
    smoke_path = _write_allowed_smoke_report(
        "test-api-review-ready.synthetic-product-gate.smoke-report.json",
        _smoke_report(recall=0.85),
    )
    tool = load_tool_module()

    report = tool.build_synthetic_product_value_gate_report(
        smoke_report_path=smoke_path,
        script_json_path=API_SCRIPT_PATH,
    )

    assert report["product_value_decision"] == "go_to_desktop_runtime_validation"
    assert report["ready_for_desktop_runtime_validation"] is True
    assert report["ready_for_real_mic_pilot"] is False
    assert report["gate_failures"] == []
    assert report["next_action"] == "validate_desktop_runtime_before_real_mic"


def test_product_value_gate_blocks_provider_errors_even_when_entity_recall_is_high():
    smoke_path = _write_allowed_smoke_report(
        "test-api-review-provider-error.synthetic-product-gate.smoke-report.json",
        _smoke_report(recall=0.95, error_count=1),
    )
    tool = load_tool_module()

    report = tool.build_synthetic_product_value_gate_report(
        smoke_report_path=smoke_path,
        script_json_path=API_SCRIPT_PATH,
    )

    assert report["product_value_decision"] == "blocked_by_event_contract"
    assert report["ready_for_desktop_runtime_validation"] is False
    assert report["gate_failures"] == ["ASR stream contains provider errors"]
    assert report["next_action"] == "fix_asr_event_contract"


def test_product_value_gate_keeps_non_engineering_control_as_negative_control():
    smoke = _smoke_report(recall=1.0)
    smoke["script_id"] = "non-engineering-control-001"
    smoke["technical_entity_count"] = 0
    smoke["normalized_technical_entity_recall"] = 1.0
    smoke["normalized_technical_entity_precision"] = 1.0
    smoke["quality_gate"]["passes_first_pilot_entity_threshold"] = True
    smoke_path = _write_allowed_smoke_report(
        "test-non-engineering-control.synthetic-product-gate.smoke-report.json",
        smoke,
    )
    tool = load_tool_module()

    report = tool.build_synthetic_product_value_gate_report(
        smoke_report_path=smoke_path,
        script_json_path=NON_ENGINEERING_SCRIPT_PATH,
    )

    assert report["product_value_decision"] == "negative_control_passed"
    assert report["is_engineering_value_script"] is False
    assert report["ready_for_desktop_runtime_validation"] is False
    assert report["ready_for_real_mic_pilot"] is False
    assert report["expected_card_count"] == 0
    assert report["expected_gap_candidates"] == []
    assert report["next_action"] == "keep_as_negative_control_in_batch_gate"


def test_product_value_gate_rejects_forbidden_or_unapproved_paths():
    tool = load_tool_module()

    report = tool.build_synthetic_product_value_gate_report_from_relative_paths(
        smoke_report_path="outputs/private.smoke-report.json",
        script_json_path="data/asr_eval/local_samples/private.json",
    )

    assert report["status"] == "blocked"
    assert "smoke_report_path is not allowed" in report["validation_errors"]
    assert "script_json_path is not allowed" in report["validation_errors"]
    assert report["safe_to_read_user_audio"] is False
    assert report["safe_to_call_llm"] is False


def test_product_value_gate_rejects_allowed_root_symlink_to_unapproved_target(tmp_path):
    outside_target = tmp_path / "outside-smoke-report.json"
    outside_target.write_text(json.dumps(_smoke_report(recall=1.0)), encoding="utf-8")
    ALLOWED_REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    symlink_path = ALLOWED_REPORT_ROOT / "test-symlink-outside.synthetic-product-gate.smoke-report.json"
    if symlink_path.exists() or symlink_path.is_symlink():
        symlink_path.unlink()
    symlink_path.symlink_to(outside_target)
    tool = load_tool_module()

    try:
        report = tool.build_synthetic_product_value_gate_report_from_relative_paths(
            smoke_report_path="artifacts/tmp/asr_reports/test-symlink-outside.synthetic-product-gate.smoke-report.json",
            script_json_path="data/asr_eval/synthetic_meetings/scripts/api-review.json",
        )
    finally:
        symlink_path.unlink(missing_ok=True)

    assert report["status"] == "blocked"
    assert "smoke_report_path resolved path is not allowed" in report["validation_errors"]
    assert report["safe_to_read_user_audio"] is False


def test_product_value_gate_direct_builder_rejects_unapproved_absolute_paths(tmp_path):
    smoke_path = tmp_path / "api-review-001.sherpa.smoke-report.json"
    script_path = tmp_path / "api-review.json"
    smoke_path.write_text(json.dumps(_smoke_report(recall=1.0)), encoding="utf-8")
    script_path.write_text("{}", encoding="utf-8")
    tool = load_tool_module()

    report = tool.build_synthetic_product_value_gate_report(
        smoke_report_path=smoke_path,
        script_json_path=script_path,
    )

    assert report["status"] == "blocked"
    assert "smoke_report_path resolved path is not allowed" in report["validation_errors"]
    assert "script_json_path resolved path is not allowed" in report["validation_errors"]
