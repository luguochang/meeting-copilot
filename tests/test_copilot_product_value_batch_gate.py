import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "copilot_product_value_batch_gate.py"


def load_tool_module():
    spec = importlib.util.spec_from_file_location(
        "copilot_product_value_batch_gate",
        TOOL_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_json(root: Path, relative_path: str, payload: object) -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return Path(relative_path)


def _script(script_id: str, scenario: str, *, engineering: bool = True) -> dict[str, object]:
    if not engineering:
        return {
            "script_id": script_id,
            "scenario": scenario,
            "turns": [
                {"speaker": "A", "text": "我们今天先确认团建时间，周五下午大家是否都方便。"},
                {"speaker": "B", "text": "名单我会后整理一下，明天发到群里。"},
            ],
            "technical_entities": [],
            "expected_gap_candidates": [],
            "expected_suggestion_cards": [],
            "expected_engineering_card_count_min": 0,
            "expected_engineering_card_count_max": 0,
        }
    return {
        "script_id": script_id,
        "scenario": scenario,
        "turns": [
            {"speaker": "A", "text": "payment-gateway 灰度期间需要看 P99 延迟。"},
            {"speaker": "B", "text": "如果错误率超过 0.1% 就回滚，负责人是谁？"},
        ],
        "technical_entities": ["payment-gateway", "P99"],
        "expected_gap_candidates": ["owner", "rollback", "metric_monitoring"],
        "expected_suggestion_cards": [
            {
                "card_id": f"{script_id}-owner-gap",
                "gap_type": "owner",
                "trigger_window_seconds": {"min": 0, "max": 30},
                "evidence_span_required": True,
                "should_show": True,
            }
        ],
        "expected_engineering_card_count_min": 1,
        "expected_engineering_card_count_max": 3,
    }


def _events(*, engineering: bool = True) -> list[dict[str, object]]:
    text = (
        "payment-gateway 灰度期间需要看 P99 延迟，回滚负责人是谁？"
        if engineering
        else "我们今天先确认团建时间，周五下午大家是否都方便。"
    )
    return [
        {
            "event_type": "final",
            "segment_id": "seg_001",
            "text": text,
            "start_ms": 0,
            "end_ms": 5000,
            "received_at_ms": 5500,
            "confidence": 0.95,
        },
        {
            "event_type": "end_of_stream",
            "segment_id": "eos",
            "text": "",
            "start_ms": 5000,
            "end_ms": 5000,
            "received_at_ms": 5600,
        },
    ]


def _smoke(script_id: str, recall: float) -> dict[str, object]:
    return {
        "report_mode": "synthetic_asr_smoke_report",
        "report_version": "synthetic_asr_smoke_report.v1",
        "status": "completed",
        "script_id": script_id,
        "normalized_technical_entity_recall": recall,
        "normalized_technical_entity_precision": 1.0,
        "event_counts": {
            "partial": 0,
            "final": 1,
            "revision": 0,
            "error": 0,
            "end_of_stream": 1,
        },
    }


def _seed_batch_inputs(root: Path, recalls: dict[str, float]) -> None:
    scripts = [
        ("api-review-001", "api_review", True),
        ("release-review-001", "release_review", True),
        ("incident-review-001", "incident_review", True),
        ("architecture-review-001", "architecture_review", True),
        ("non-engineering-control-001", "non_engineering_control", False),
    ]
    for script_id, scenario, engineering in scripts:
        script_name = script_id.removesuffix("-001")
        _write_json(
            root,
            f"data/asr_eval/synthetic_meetings/scripts/{script_name}.json",
            _script(script_id, scenario, engineering=engineering),
        )
        _write_json(
            root,
            f"artifacts/tmp/asr_events/{script_id}.mock.events.json",
            _events(engineering=engineering),
        )
        _write_json(
            root,
            f"artifacts/tmp/asr_events/{script_id}.sherpa.events.json",
            _events(engineering=engineering),
        )
        _write_json(
            root,
            f"artifacts/tmp/asr_reports/{script_id}.sherpa.smoke-report.json",
            _smoke(script_id, recalls.get(script_id, 1.0)),
        )


def test_batch_gate_summarizes_five_synthetic_scenarios_and_preserves_failure_causes(tmp_path, monkeypatch):
    tool = load_tool_module()
    monkeypatch.setattr(tool, "REPO_ROOT", tmp_path)
    _seed_batch_inputs(
        tmp_path,
        {
            "api-review-001": 0.5,
            "release-review-001": 0.25,
            "incident-review-001": 0.0,
            "architecture-review-001": 0.0,
            "non-engineering-control-001": 1.0,
        },
    )

    report = tool.build_copilot_product_value_batch_report_from_relative_roots(
        scripts_root="data/asr_eval/synthetic_meetings/scripts",
        mock_events_pattern="artifacts/tmp/asr_events/{script_id}.mock.events.json",
        real_events_pattern="artifacts/tmp/asr_events/{script_id}.sherpa.events.json",
        real_smoke_report_pattern="artifacts/tmp/asr_reports/{script_id}.sherpa.smoke-report.json",
        real_provider="sherpa_onnx_streaming",
    )

    assert report["report_mode"] == "copilot_product_value_batch_gate"
    assert report["report_version"] == "copilot_product_value_batch_gate.v1"
    assert report["status"] == "completed"
    assert report["scenario_count"] == 5
    assert report["engineering_scenario_count"] == 4
    assert report["negative_control_count"] == 1
    assert report["overall_decision"] == "blocked_by_asr_quality"
    assert report["next_action"] == "improve_real_asr_quality_or_prepare_model_approval"
    assert report["decision_counts"]["blocked_by_asr_quality"] == 4
    assert report["decision_counts"]["product_logic_ready"] == 1
    assert report["perfect_lane_ready_count"] == 5
    assert report["mock_lane_ready_count"] == 5
    assert report["real_asr_blocked_count"] == 4
    assert report["non_engineering_candidate_count"] == 0
    assert report["scenario_ids"] == [
        "api-review-001",
        "architecture-review-001",
        "incident-review-001",
        "non-engineering-control-001",
        "release-review-001",
    ]
    assert report["safe_to_call_llm_now"] is False
    assert report["safe_to_call_remote_asr_now"] is False
    assert report["safe_to_capture_microphone_now"] is False
    assert report["safe_to_read_user_audio_now"] is False
    assert report["safe_to_read_configs_local_now"] is False
    assert report["safe_to_download_models_now"] is False

    by_id = {scenario["scenario_id"]: scenario for scenario in report["scenarios"]}
    assert by_id["api-review-001"]["overall_decision"] == "blocked_by_asr_quality"
    assert by_id["non-engineering-control-001"]["overall_decision"] == "product_logic_ready"
    assert by_id["non-engineering-control-001"]["non_engineering_candidate_count"] == 0
    assert by_id["release-review-001"]["real_asr_lane_decision"] == "blocked_by_asr_quality"

    report_json = json.dumps(report, ensure_ascii=False)
    assert str(tmp_path) not in report_json
    assert "/Users/" not in report_json


def test_batch_gate_blocks_if_any_scenario_input_is_missing_or_forbidden(tmp_path, monkeypatch):
    tool = load_tool_module()
    monkeypatch.setattr(tool, "REPO_ROOT", tmp_path)
    _seed_batch_inputs(tmp_path, {"api-review-001": 1.0})
    forbidden_root = tmp_path / "configs" / "local"
    forbidden_root.mkdir(parents=True)
    target = forbidden_root / "linked-events.json"
    target.write_text(json.dumps(_events(), ensure_ascii=False), encoding="utf-8")
    visible = tmp_path / "artifacts" / "tmp" / "asr_events" / "api-review-001.mock.events.json"
    visible.unlink()
    visible.symlink_to(target)

    report = tool.build_copilot_product_value_batch_report_from_relative_roots(
        scripts_root="data/asr_eval/synthetic_meetings/scripts",
        mock_events_pattern="artifacts/tmp/asr_events/{script_id}.mock.events.json",
        real_events_pattern="outputs/{script_id}.events.json",
        real_smoke_report_pattern="artifacts/tmp/asr_reports/{script_id}.sherpa.smoke-report.json",
        real_provider="sherpa_onnx_streaming",
    )

    assert report["status"] == "blocked"
    assert report["overall_decision"] == "blocked_by_input_validation"
    assert any("mock_events_path resolved path is forbidden: configs/local" in error for error in report["validation_errors"])
    assert any("real_events_path is not allowed" in error for error in report["validation_errors"])
    assert report["scenarios"] == []
    assert report["safe_to_call_llm_now"] is False
    assert report["safe_to_read_configs_local_now"] is False


def test_batch_gate_cli_runs_against_relative_roots(tmp_path, monkeypatch, capsys):
    tool = load_tool_module()
    monkeypatch.setattr(tool, "REPO_ROOT", tmp_path)
    _seed_batch_inputs(tmp_path, {"api-review-001": 0.5})

    exit_code = tool.main(
        [
            "--scripts-root",
            "data/asr_eval/synthetic_meetings/scripts",
            "--mock-events-pattern",
            "artifacts/tmp/asr_events/{script_id}.mock.events.json",
            "--real-events-pattern",
            "artifacts/tmp/asr_events/{script_id}.sherpa.events.json",
            "--real-smoke-report-pattern",
            "artifacts/tmp/asr_reports/{script_id}.sherpa.smoke-report.json",
            "--real-provider",
            "sherpa_onnx_streaming",
        ]
    )

    assert exit_code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["report_mode"] == "copilot_product_value_batch_gate"
    assert report["scenario_count"] == 5
    assert report["overall_decision"] == "blocked_by_asr_quality"
    assert report["safe_to_call_llm_now"] is False


def test_batch_gate_cli_defaults_keep_mock_and_real_asr_event_lanes_separate():
    tool = load_tool_module()

    args = tool.parse_args([])

    assert args.mock_events_pattern == "artifacts/tmp/asr_events/{script_id}.mock.events.json"
    assert args.real_events_pattern == "artifacts/tmp/asr_events/{script_id}.sherpa.events.json"
    assert args.mock_events_pattern != args.real_events_pattern


def test_batch_gate_source_has_no_remote_model_audio_or_process_entrypoints():
    source = TOOL_PATH.read_text(encoding="utf-8")

    forbidden_source_tokens = [
        "subprocess",
        "os.system",
        "Popen",
        "check_call",
        "check_output",
        "requests.",
        "httpx.",
        "urllib.",
        "sounddevice",
        "pyaudio",
        "AVAudio",
        "ScreenCaptureKit",
        "AutoModel",
        "run(",
        "exec(",
        "eval(",
    ]
    for token in forbidden_source_tokens:
        assert token not in source
