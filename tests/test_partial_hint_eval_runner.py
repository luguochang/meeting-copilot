import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "partial_hint_eval_runner.py"
DATASET_PATH = REPO_ROOT / "data" / "asr_eval" / "partial_hint" / "partial_hint_cases.json"


def load_tool_module():
    spec = importlib.util.spec_from_file_location("partial_hint_eval_runner", TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_partial_hint_eval_dataset_has_real_error_and_control_coverage():
    dataset = json.loads(DATASET_PATH.read_text(encoding="utf-8"))

    case_ids = {case["case_id"] for case in dataset["cases"]}
    assert "real_mic_asr_error_release_gray_percent" in case_ids
    assert "real_mic_asr_error_kafka_owner" in case_ids
    assert "closed_action_should_not_hint" in case_ids
    assert "non_technical_interruption_should_not_hint" in case_ids
    assert "progressive_risk_duplicate_3" in case_ids

    assert len(dataset["cases"]) >= 12
    assert all("expected_hint_type" in case for case in dataset["cases"])
    assert all("tags" in case for case in dataset["cases"])


def test_partial_hint_eval_runner_scores_dataset_and_reports_no_remote_calls(tmp_path, monkeypatch):
    tool = load_tool_module()
    monkeypatch.setattr(tool, "REPO_ROOT", REPO_ROOT)

    report = tool.build_partial_hint_eval_report(
        dataset_path=DATASET_PATH,
        output_dir=tmp_path,
    )

    assert report["report_mode"] == "partial_hint_eval"
    assert report["report_version"] == "partial_hint_eval.v1"
    assert report["status"] == "passed"
    assert report["case_count"] >= 12
    assert report["failed_count"] == 0
    assert report["remote_llm_called"] is False
    assert report["remote_asr_called"] is False
    assert report["duplicate_suppression"]["progressive_duplicate_hint_count"] == 1
    assert report["metrics"]["precision"] >= 0.8
    assert report["metrics"]["recall"] >= 0.8
    assert (tmp_path / "summary.json").is_file()
