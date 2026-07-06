import importlib.util
import hashlib
import io
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "funasr_synthetic_smoke_result_evidence.py"


EXPECTED_FALSE_FLAGS = [
    "safe_to_run_asr_now",
    "safe_to_download_models_now",
    "safe_to_capture_microphone_now",
    "safe_to_read_user_audio_now",
    "safe_to_read_configs_local_now",
    "safe_to_call_remote_asr_now",
    "safe_to_call_llm_now",
    "safe_to_download_public_audio_now",
    "safe_to_read_audio_file_now",
]


def load_tool_module():
    spec = importlib.util.spec_from_file_location(
        "funasr_synthetic_smoke_result_evidence",
        TOOL_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def engineering_result(scenario_id: str = "api-review-001") -> dict:
    return {
        "scenario_id": scenario_id,
        "scenario_kind": "engineering",
        "input_source_kind": "synthetic_audio",
        "event_contract": {
            "partial_count": 3,
            "final_count": 3,
            "revision_count": 1,
            "error_count": 0,
            "end_of_stream_count": 1,
            "has_required_event_sequence": True,
        },
        "latency_metrics": {
            "first_partial_latency_seconds_p95": 1.4,
            "final_latency_seconds_p95": 6.8,
            "suggestion_candidate_latency_seconds_p95": 18.0,
        },
        "asr_metrics": {
            "rtf": 0.32,
        },
        "technical_entity_metrics": {
            "raw_recall": 0.82,
            "normalized_recall": 0.86,
        },
        "closure": {
            "evidence_span_count": 2,
            "state_event_count": 1,
            "candidate_card_count": 1,
            "all_cards_have_evidence_spans": True,
        },
        "safety": {
            "used_microphone": False,
            "read_user_audio": False,
            "called_remote_asr": False,
            "called_llm": False,
            "downloaded_model": False,
            "downloaded_public_audio": False,
            "read_configs_local": False,
        },
    }


def negative_control_result() -> dict:
    result = engineering_result("non-engineering-control-001")
    result["scenario_kind"] = "negative_control"
    result["technical_entity_metrics"] = {
        "raw_recall": 0.0,
        "normalized_recall": 0.0,
    }
    result["closure"] = {
        "evidence_span_count": 1,
        "state_event_count": 0,
        "candidate_card_count": 0,
        "all_cards_have_evidence_spans": True,
    }
    return result


def valid_single_smoke_result() -> dict:
    return {
        "manifest_version": "funasr_synthetic_smoke_result.v1",
        "evidence_kind": "single_synthetic_smoke",
        "provider": "funasr_streaming",
        "model_alias": "paraformer-zh-streaming",
        "source_boundary": "synthetic_audio_no_user_audio",
        "scenario_results": [engineering_result()],
    }


def valid_batch_result() -> dict:
    result = valid_single_smoke_result()
    result["evidence_kind"] = "batch_synthetic_confirmation"
    result["scenario_results"] = [
        engineering_result("api-review-001"),
        engineering_result("architecture-review-001"),
        engineering_result("incident-review-001"),
        engineering_result("release-review-001"),
        negative_control_result(),
    ]
    return result


def add_valid_batch_artifact_provenance(
    evidence: dict,
    *,
    repo_root: Path,
    artifact_root: str = "artifacts/tmp/asr_reports",
) -> dict:
    artifacts = []
    root = repo_root / artifact_root
    root.mkdir(parents=True, exist_ok=True)
    for scenario in evidence["scenario_results"]:
        scenario_id = scenario["scenario_id"]
        artifact_path = root / f"{scenario_id}.funasr-smoke-scenario.json"
        artifact_path.write_text(
            json.dumps(
                {
                    "artifact_kind": "scenario_gate_report",
                    "scenario_id": scenario_id,
                    "source_kind": "local_funasr_synthetic_smoke_artifacts",
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        artifacts.append(
            {
                "artifact_kind": "scenario_gate_report",
                "scenario_id": scenario_id,
                "path": f"{artifact_root}/{artifact_path.name}",
                "sha256": hashlib.sha256(artifact_path.read_bytes()).hexdigest(),
            }
        )
    evidence["batch_artifact_provenance"] = {
        "source_kind": "local_funasr_synthetic_smoke_artifacts",
        "artifacts": artifacts,
    }
    return evidence


def test_default_report_specifies_drv044_contract_without_execution():
    tool = load_tool_module()

    report = tool.build_funasr_synthetic_smoke_result_evidence_gate()

    assert report["decision_id"] == "DRV-044"
    assert report["report_mode"] == "funasr_synthetic_smoke_result_evidence_gate"
    assert report["schema_version"] == "funasr_synthetic_smoke_result.v1"
    assert report["evidence_status"] == "not_provided"
    assert report["quality_evidence_status"] == "not_evaluated"
    assert report["approved_evidence_report_root"] == "artifacts/tmp/asr_reports"
    assert report["thresholds"]["normalized_technical_entity_recall_min"] == 0.8
    assert report["thresholds"]["first_partial_latency_seconds_p95_max"] == 2.0
    assert report["thresholds"]["final_latency_seconds_p95_max"] == 8.0
    assert report["thresholds"]["asr_rtf_max"] == 0.6
    assert report["thresholds"]["suggestion_candidate_latency_seconds_p95_max"] == 30.0
    for flag in EXPECTED_FALSE_FLAGS:
        assert report[flag] is False


def test_single_engineering_smoke_result_becomes_candidate_not_go_evidence():
    tool = load_tool_module()

    report = tool.build_funasr_synthetic_smoke_result_evidence_gate(
        evidence_report=valid_single_smoke_result(),
    )

    assert report["evidence_status"] == "schema_validated_no_asr_execution"
    assert report["quality_evidence_status"] == (
        "funasr_synthetic_smoke_quality_candidate_requires_batch_confirmation"
    )
    assert report["counts_as_asr_quality_go_evidence"] is False
    assert report["scenario_summary"]["engineering_scenario_count"] == 1
    assert report["scenario_summary"]["negative_control_count"] == 0
    assert report["scenario_summary"]["engineering_min_normalized_recall"] == 0.86
    assert report["next_action"] == "run_batch_confirmation_before_quality_exit"
    assert report["safe_to_run_asr_now"] is False
    assert report["safe_to_call_remote_asr_now"] is False


def test_batch_confirmation_requires_artifact_provenance_before_counting_as_go():
    tool = load_tool_module()

    report = tool.build_funasr_synthetic_smoke_result_evidence_gate(
        evidence_report=valid_batch_result(),
    )

    assert report["evidence_status"] == "blocked_by_quality_thresholds"
    assert report["quality_evidence_status"] == "blocked"
    assert "batch confirmation requires artifact provenance" in report["validation_errors"]
    assert report["counts_as_asr_quality_go_evidence"] is False
    assert report["batch_artifact_provenance_status"] == "missing"


def test_batch_confirmation_blocks_fixture_only_provenance():
    tool = load_tool_module()
    evidence = valid_batch_result()
    evidence["batch_artifact_provenance"] = {
        "source_kind": "fixture_only",
        "artifacts": [],
    }

    report = tool.build_funasr_synthetic_smoke_result_evidence_gate(evidence_report=evidence)

    assert report["evidence_status"] == "blocked_by_quality_thresholds"
    assert report["quality_evidence_status"] == "blocked"
    assert "batch artifact provenance source_kind must be local_funasr_synthetic_smoke_artifacts" in report[
        "validation_errors"
    ]
    assert report["counts_as_asr_quality_go_evidence"] is False
    assert report["batch_artifact_provenance_status"] == "blocked"


def test_batch_confirmation_validates_artifact_sha256_under_approved_root(tmp_path, monkeypatch):
    tool = load_tool_module()
    repo_root = tmp_path / "repo"
    monkeypatch.setattr(tool, "REPO_ROOT", repo_root)
    evidence = add_valid_batch_artifact_provenance(valid_batch_result(), repo_root=repo_root)

    report = tool.build_funasr_synthetic_smoke_result_evidence_gate(evidence_report=evidence)

    assert report["evidence_status"] == "schema_validated_no_asr_execution"
    assert report["quality_evidence_status"] == "funasr_synthetic_smoke_quality_batch_confirmed"
    assert report["counts_as_asr_quality_go_evidence"] is True
    assert report["counts_as_real_mic_go_evidence"] is False
    assert report["scenario_summary"]["engineering_scenario_count"] == 4
    assert report["scenario_summary"]["negative_control_count"] == 1
    assert report["scenario_summary"]["negative_control_candidate_cards"] == 0
    assert report["next_action"] == "allow_asr_quality_gate_to_exit_without_claiming_real_mic_ready"
    assert report["batch_artifact_provenance_status"] == "validated"
    assert report["batch_artifact_count"] == 5
    report_json = json.dumps(report, ensure_ascii=False)
    assert str(tmp_path) not in report_json
    assert "/Users/" not in report_json


def test_batch_confirmation_blocks_bad_artifact_sha256(tmp_path, monkeypatch):
    tool = load_tool_module()
    repo_root = tmp_path / "repo"
    monkeypatch.setattr(tool, "REPO_ROOT", repo_root)
    evidence = add_valid_batch_artifact_provenance(valid_batch_result(), repo_root=repo_root)
    evidence["batch_artifact_provenance"]["artifacts"][0]["sha256"] = "0" * 64

    report = tool.build_funasr_synthetic_smoke_result_evidence_gate(evidence_report=evidence)

    assert report["evidence_status"] == "blocked_by_quality_thresholds"
    assert report["quality_evidence_status"] == "blocked"
    assert "artifact sha256 mismatch for api-review-001" in report["validation_errors"]
    assert report["counts_as_asr_quality_go_evidence"] is False
    assert report["batch_artifact_provenance_status"] == "blocked"


def test_smoke_result_blocks_low_recall_missing_event_contract_and_safety_side_effects():
    tool = load_tool_module()
    evidence = valid_single_smoke_result()
    evidence["scenario_results"][0]["technical_entity_metrics"]["normalized_recall"] = 0.79
    evidence["scenario_results"][0]["event_contract"]["has_required_event_sequence"] = False
    evidence["scenario_results"][0]["safety"]["called_remote_asr"] = True

    report = tool.build_funasr_synthetic_smoke_result_evidence_gate(evidence_report=evidence)

    assert report["evidence_status"] == "blocked_by_quality_thresholds"
    assert report["quality_evidence_status"] == "blocked"
    assert "engineering normalized_recall must be >= 0.8" in report["validation_errors"]
    assert "has_required_event_sequence must be true" in report["validation_errors"]
    assert "called_remote_asr must be false" in report["validation_errors"]
    assert report["counts_as_asr_quality_go_evidence"] is False
    assert report["safe_to_call_remote_asr_now"] is False


def test_smoke_result_blocks_inconsistent_entity_detail_metrics():
    tool = load_tool_module()
    evidence = valid_single_smoke_result()
    evidence["scenario_results"][0]["technical_entity_metrics"] = {
        "raw_recall": 1.0,
        "normalized_recall": 1.0,
        "expected_entities": ["payment-gateway", "request_id"],
        "raw_matched_entities": ["payment-gateway"],
        "raw_missing_entities": ["request_id"],
        "normalized_matched_entities": ["payment-gateway"],
        "normalized_missing_entities": [],
    }

    report = tool.build_funasr_synthetic_smoke_result_evidence_gate(evidence_report=evidence)

    assert report["evidence_status"] == "blocked_by_quality_thresholds"
    assert report["quality_evidence_status"] == "blocked"
    assert "raw_recall must match raw_matched_entities / expected_entities" in report["validation_errors"]
    assert (
        "normalized_recall must match normalized_matched_entities / expected_entities"
        in report["validation_errors"]
    )
    assert "normalized matched/missing entities must cover expected_entities" in report["validation_errors"]
    assert report["counts_as_asr_quality_go_evidence"] is False


def test_smoke_result_blocks_negative_control_fake_candidate():
    tool = load_tool_module()
    evidence = valid_batch_result()
    evidence["scenario_results"][-1]["closure"]["candidate_card_count"] = 1

    report = tool.build_funasr_synthetic_smoke_result_evidence_gate(evidence_report=evidence)

    assert report["evidence_status"] == "blocked_by_quality_thresholds"
    assert report["quality_evidence_status"] == "blocked"
    assert "negative control candidate_card_count must be 0" in report["validation_errors"]
    assert report["counts_as_asr_quality_go_evidence"] is False


def test_smoke_result_path_guard_blocks_forbidden_path_before_reading(monkeypatch):
    tool = load_tool_module()

    def fail_if_read(*args, **kwargs):
        raise AssertionError("evidence report was read before path guard")

    monkeypatch.setattr(Path, "read_text", fail_if_read)

    report = tool.build_funasr_synthetic_smoke_result_evidence_gate(
        evidence_report_path="configs/local/funasr-smoke-result.json",
    )

    assert report["evidence_status"] == "blocked_by_path_guard"
    assert report["evidence_report_read_status"] == "blocked"
    assert report["quality_evidence_status"] == "blocked"
    assert report["validation_errors"] == ["evidence_report_path is blocked: configs/local"]
    assert report["safe_to_read_configs_local_now"] is False


def test_smoke_result_cli_loads_allowed_path_and_redacts_absolute_path(tmp_path, monkeypatch):
    tool = load_tool_module()
    repo_root = tmp_path / "repo"
    report_root = repo_root / "artifacts" / "tmp" / "asr_reports"
    report_root.mkdir(parents=True)
    evidence_path = report_root / "funasr-smoke-result.json"
    evidence_path.write_text(json.dumps(valid_single_smoke_result()), encoding="utf-8")
    monkeypatch.setattr(tool, "REPO_ROOT", repo_root)

    out = io.StringIO()
    exit_code = tool.main(["--evidence-report-path", str(evidence_path)], out=out)

    report = json.loads(out.getvalue())
    assert exit_code == 1
    assert report["evidence_report_path"] == "artifacts/tmp/asr_reports/funasr-smoke-result.json"
    assert report["quality_evidence_status"] == (
        "funasr_synthetic_smoke_quality_candidate_requires_batch_confirmation"
    )
    assert str(tmp_path) not in out.getvalue()
