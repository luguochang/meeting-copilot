import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "copilot_product_value_tri_lane_gate.py"


def load_tool_module():
    spec = importlib.util.spec_from_file_location(
        "copilot_product_value_tri_lane_gate",
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


def _api_review_script() -> dict[str, object]:
    return {
        "script_id": "api-review-001",
        "scenario": "api_review",
        "language": "zh-CN",
        "turns": [
            {
                "speaker": "A",
                "text": "我们先看 payment-gateway 的创建订单接口，字段 request_id 要兼容旧客户端。",
            },
            {
                "speaker": "B",
                "text": "错误码先沿用 40012，但是灰度期间需要看 P99 延迟。",
            },
            {
                "speaker": "C",
                "text": "兼容逻辑可以本周合进去，owner 我们会后再确认。",
            },
        ],
        "technical_entities": ["payment-gateway", "request_id", "40012", "P99"],
        "expected_gap_candidates": ["owner", "rollback", "metric_monitoring"],
        "expected_suggestion_cards": [
            {
                "card_id": "api-review-001-owner-gap",
                "gap_type": "owner",
                "suggested_question": "是否需要现在确认 payment-gateway 兼容逻辑的 owner？",
                "evidence_span_required": True,
                "trigger_window_seconds": {"min": 0, "max": 30},
                "should_show": True,
            },
            {
                "card_id": "api-review-001-rollback-gap",
                "gap_type": "rollback",
                "suggested_question": "是否需要补一句灰度失败时的回滚条件和执行人？",
                "evidence_span_required": True,
                "trigger_window_seconds": {"min": 0, "max": 30},
                "should_show": True,
            },
        ],
        "expected_engineering_card_count_min": 1,
        "expected_engineering_card_count_max": 3,
    }


def _architecture_review_script() -> dict[str, object]:
    return {
        "script_id": "architecture-review-001",
        "scenario": "architecture_review",
        "language": "zh-CN",
        "turns": [
            {
                "speaker": "A",
                "text": "新的 recommendation-service 会依赖 feature-store 和 redis cluster。",
            },
            {
                "speaker": "B",
                "text": "QPS 峰值按两万估，缓存穿透时可能会打到 mysql。",
            },
            {
                "speaker": "C",
                "text": "降级方案先写在设计文档里，压测 owner 还没安排。",
            },
        ],
        "technical_entities": [
            "recommendation-service",
            "feature-store",
            "redis cluster",
            "QPS",
            "mysql",
        ],
        "expected_gap_candidates": ["owner", "test_verification", "rollback"],
        "expected_suggestion_cards": [
            {
                "card_id": "architecture-review-001-test-gap",
                "gap_type": "test_verification",
                "suggested_question": "是否需要现在确认压测 owner 和压测通过阈值？",
                "evidence_span_required": True,
                "trigger_window_seconds": {"min": 0, "max": 30},
                "should_show": True,
            },
            {
                "card_id": "architecture-review-001-rollback-gap",
                "gap_type": "rollback",
                "suggested_question": "缓存穿透风险如果出现，是否需要明确降级触发条件？",
                "evidence_span_required": True,
                "trigger_window_seconds": {"min": 0, "max": 30},
                "should_show": True,
            },
        ],
        "expected_engineering_card_count_min": 1,
        "expected_engineering_card_count_max": 3,
    }


def _non_engineering_script() -> dict[str, object]:
    return {
        "script_id": "non-engineering-control-001",
        "scenario": "non_engineering_control",
        "language": "zh-CN",
        "turns": [
            {"speaker": "A", "text": "我们今天先确认团建时间，周五下午大家是否都方便。"},
            {"speaker": "B", "text": "预算可以按每人两百来估，地点选交通方便一点的地方。"},
            {"speaker": "C", "text": "名单我会后整理一下，明天发到群里。"},
        ],
        "technical_entities": [],
        "expected_gap_candidates": [],
        "expected_suggestion_cards": [],
        "expected_engineering_card_count_min": 0,
        "expected_engineering_card_count_max": 0,
    }


def _weak_real_smoke_report() -> dict[str, object]:
    return {
        "report_mode": "synthetic_asr_smoke_report",
        "report_version": "synthetic_asr_smoke_report.v1",
        "status": "completed",
        "script_id": "api-review-001",
        "normalized_technical_entity_recall": 0.5,
        "normalized_technical_entity_precision": 1.0,
        "event_counts": {
            "partial": 1,
            "final": 1,
            "revision": 0,
            "error": 0,
            "end_of_stream": 1,
        },
    }


def _good_smoke_report(script_id: str) -> dict[str, object]:
    return {
        "report_mode": "synthetic_asr_smoke_report",
        "report_version": "synthetic_asr_smoke_report.v1",
        "status": "completed",
        "script_id": script_id,
        "normalized_technical_entity_recall": 1.0,
        "normalized_technical_entity_precision": 1.0,
        "event_counts": {
            "partial": 0,
            "final": 1,
            "revision": 0,
            "error": 0,
            "end_of_stream": 1,
        },
    }


def _api_review_events() -> list[dict[str, object]]:
    return [
        {
            "event_type": "final",
            "segment_id": "seg_api_review",
            "text": "payment-gateway 灰度期间需要看 P99 延迟，回滚负责人是谁？",
            "start_ms": 0,
            "end_ms": 6000,
            "received_at_ms": 6500,
            "confidence": 0.94,
        },
        {
            "event_type": "end_of_stream",
            "segment_id": "eos",
            "text": "",
            "start_ms": 6000,
            "end_ms": 6000,
            "received_at_ms": 6600,
        },
    ]


def _architecture_review_events() -> list[dict[str, object]]:
    return [
        {
            "event_type": "final",
            "segment_id": "seg_architecture_risk",
            "text": "QPS 峰值按两万估，缓存穿透时可能会打到 mysql。",
            "start_ms": 0,
            "end_ms": 6000,
            "received_at_ms": 6500,
            "confidence": 0.95,
        },
        {
            "event_type": "final",
            "segment_id": "seg_architecture_owner",
            "text": "降级方案先写在设计文档里，压测 owner 还没安排。",
            "start_ms": 6000,
            "end_ms": 12000,
            "received_at_ms": 12500,
            "confidence": 0.95,
        },
        {
            "event_type": "end_of_stream",
            "segment_id": "eos",
            "text": "",
            "start_ms": 12000,
            "end_ms": 12000,
            "received_at_ms": 12600,
        },
    ]


def _non_engineering_events() -> list[dict[str, object]]:
    return [
        {
            "event_type": "final",
            "segment_id": "seg_social",
            "text": "我们今天先确认团建时间，周五下午大家是否都方便。",
            "start_ms": 0,
            "end_ms": 4000,
            "received_at_ms": 4500,
            "confidence": 0.96,
        },
        {
            "event_type": "end_of_stream",
            "segment_id": "eos",
            "text": "",
            "start_ms": 4000,
            "end_ms": 4000,
            "received_at_ms": 4600,
        },
    ]


def _lane_by_id(report: dict[str, object]) -> dict[str, dict[str, object]]:
    return {lane["lane"]: lane for lane in report["lanes"]}


def test_tri_lane_gate_separates_product_logic_from_real_asr_quality(tmp_path, monkeypatch):
    tool = load_tool_module()
    monkeypatch.setattr(tool, "REPO_ROOT", tmp_path)
    script_path = _write_json(
        tmp_path,
        "data/asr_eval/synthetic_meetings/scripts/api-review.json",
        _api_review_script(),
    )
    mock_events_path = _write_json(
        tmp_path,
        "artifacts/tmp/asr_events/api-review-001.mock.events.json",
        _api_review_events(),
    )
    real_events_path = _write_json(
        tmp_path,
        "artifacts/tmp/asr_events/api-review-001.sherpa.events.json",
        _api_review_events(),
    )
    real_smoke_path = _write_json(
        tmp_path,
        "artifacts/tmp/asr_reports/api-review-001.sherpa.smoke-report.json",
        _weak_real_smoke_report(),
    )

    report = tool.build_copilot_product_value_tri_lane_report_from_relative_paths(
        script_json_path=script_path.as_posix(),
        mock_events_path=mock_events_path.as_posix(),
        real_events_path=real_events_path.as_posix(),
        real_smoke_report_path=real_smoke_path.as_posix(),
        real_provider="sherpa_onnx_streaming",
    )

    assert report["report_mode"] == "copilot_product_value_tri_lane_gate"
    assert report["report_version"] == "copilot_product_value_tri_lane_gate.v1"
    assert report["status"] == "completed"
    assert report["scenario_id"] == "api-review-001"
    assert report["overall_decision"] == "blocked_by_asr_quality"
    assert report["next_action"] == "improve_real_asr_quality_or_prepare_model_approval"
    assert report["lane_count"] == 3
    assert report["feedback_rubric_required"] is True
    assert set(report["feedback_labels"]) == {
        "useful",
        "would_have_asked",
        "wrong",
        "too_late",
        "too_intrusive",
        "dismissed",
    }
    assert report["safe_to_call_llm_now"] is False
    assert report["safe_to_call_remote_asr_now"] is False
    assert report["safe_to_capture_microphone_now"] is False
    assert report["safe_to_read_user_audio_now"] is False
    assert report["safe_to_read_configs_local_now"] is False

    lanes = _lane_by_id(report)
    assert lanes["perfect_transcript"]["decision"] == "product_logic_ready"
    assert lanes["perfect_transcript"]["candidate_count"] >= 1
    assert lanes["perfect_transcript"]["evidence_span_count"] >= 1
    assert lanes["perfect_transcript"]["candidate_latency_window_status"] == "within_expected_window"
    assert lanes["mock_asr"]["decision"] == "product_logic_ready"
    assert lanes["mock_asr"]["llm_request_draft_count"] >= 1
    assert lanes["real_asr"]["decision"] == "blocked_by_asr_quality"
    assert lanes["real_asr"]["normalized_technical_entity_recall"] == 0.5
    assert "normalized technical entity recall below first-pilot threshold" in lanes["real_asr"]["block_reasons"]

    report_json = json.dumps(report, ensure_ascii=False)
    assert str(tmp_path) not in report_json
    assert "/Users/" not in report_json


def test_tri_lane_gate_architecture_review_perfect_lane_detects_evidence_backed_gap(tmp_path, monkeypatch):
    tool = load_tool_module()
    monkeypatch.setattr(tool, "REPO_ROOT", tmp_path)
    script_path = _write_json(
        tmp_path,
        "data/asr_eval/synthetic_meetings/scripts/architecture-review.json",
        _architecture_review_script(),
    )
    mock_events_path = _write_json(
        tmp_path,
        "artifacts/tmp/asr_events/architecture-review-001.mock.events.json",
        _architecture_review_events(),
    )
    real_events_path = _write_json(
        tmp_path,
        "artifacts/tmp/asr_events/architecture-review-001.real.events.json",
        _architecture_review_events(),
    )
    real_smoke_path = _write_json(
        tmp_path,
        "artifacts/tmp/asr_reports/architecture-review-001.real.smoke-report.json",
        _good_smoke_report("architecture-review-001"),
    )

    report = tool.build_copilot_product_value_tri_lane_report_from_relative_paths(
        script_json_path=script_path.as_posix(),
        mock_events_path=mock_events_path.as_posix(),
        real_events_path=real_events_path.as_posix(),
        real_smoke_report_path=real_smoke_path.as_posix(),
        real_provider="sherpa_onnx_streaming",
    )

    lanes = _lane_by_id(report)
    assert report["overall_decision"] == "product_logic_ready"
    assert lanes["perfect_transcript"]["decision"] == "product_logic_ready"
    assert lanes["perfect_transcript"]["candidate_count"] >= 1
    assert lanes["perfect_transcript"]["evidence_span_count"] >= 1
    assert lanes["perfect_transcript"]["candidate_latency_window_status"] == "within_expected_window"
    assert lanes["perfect_transcript"]["non_engineering_candidate_count"] == 0


def test_tri_lane_gate_blocks_when_perfect_transcript_cannot_find_expected_gap(tmp_path, monkeypatch):
    tool = load_tool_module()
    monkeypatch.setattr(tool, "REPO_ROOT", tmp_path)
    script = _api_review_script()
    script["turns"] = [
        {"speaker": "A", "text": "我们先简单同步一下背景信息。"},
        {"speaker": "B", "text": "这个事情后面再单独看。"},
    ]
    script_path = _write_json(
        tmp_path,
        "data/asr_eval/synthetic_meetings/scripts/product-logic-fail.json",
        script,
    )
    mock_events_path = _write_json(
        tmp_path,
        "artifacts/tmp/asr_events/product-logic-fail.mock.events.json",
        _api_review_events(),
    )
    real_events_path = _write_json(
        tmp_path,
        "artifacts/tmp/asr_events/product-logic-fail.real.events.json",
        _api_review_events(),
    )
    real_smoke_path = _write_json(
        tmp_path,
        "artifacts/tmp/asr_reports/product-logic-fail.real.smoke-report.json",
        _good_smoke_report("api-review-001"),
    )

    report = tool.build_copilot_product_value_tri_lane_report_from_relative_paths(
        script_json_path=script_path.as_posix(),
        mock_events_path=mock_events_path.as_posix(),
        real_events_path=real_events_path.as_posix(),
        real_smoke_report_path=real_smoke_path.as_posix(),
        real_provider="sherpa_onnx_streaming",
    )

    lanes = _lane_by_id(report)
    assert report["overall_decision"] == "blocked_by_product_logic"
    assert report["next_action"] == "fix_gap_detection_before_more_asr_work"
    assert lanes["perfect_transcript"]["decision"] == "blocked_by_product_logic"
    assert lanes["perfect_transcript"]["candidate_count"] == 0
    assert "expected engineering gaps were not detected" in lanes["perfect_transcript"]["block_reasons"]


def test_tri_lane_gate_keeps_non_engineering_control_at_zero_candidates(tmp_path, monkeypatch):
    tool = load_tool_module()
    monkeypatch.setattr(tool, "REPO_ROOT", tmp_path)
    script_path = _write_json(
        tmp_path,
        "data/asr_eval/synthetic_meetings/scripts/non-engineering-control.json",
        _non_engineering_script(),
    )
    mock_events_path = _write_json(
        tmp_path,
        "artifacts/tmp/asr_events/non-engineering-control.mock.events.json",
        _non_engineering_events(),
    )
    real_events_path = _write_json(
        tmp_path,
        "artifacts/tmp/asr_events/non-engineering-control.real.events.json",
        _non_engineering_events(),
    )
    real_smoke_path = _write_json(
        tmp_path,
        "artifacts/tmp/asr_reports/non-engineering-control.real.smoke-report.json",
        _good_smoke_report("non-engineering-control-001"),
    )

    report = tool.build_copilot_product_value_tri_lane_report_from_relative_paths(
        script_json_path=script_path.as_posix(),
        mock_events_path=mock_events_path.as_posix(),
        real_events_path=real_events_path.as_posix(),
        real_smoke_report_path=real_smoke_path.as_posix(),
        real_provider="sherpa_onnx_streaming",
    )

    assert report["overall_decision"] == "product_logic_ready"
    assert report["is_engineering_value_script"] is False
    assert report["non_engineering_candidate_count"] == 0
    for lane in report["lanes"]:
        assert lane["decision"] == "product_logic_ready"
        assert lane["candidate_count"] == 0
        assert lane["non_engineering_candidate_count"] == 0
        assert lane["candidate_latency_window_status"] == "not_applicable"


def test_tri_lane_gate_rejects_forbidden_or_symlink_inputs_before_reading(tmp_path, monkeypatch):
    tool = load_tool_module()
    monkeypatch.setattr(tool, "REPO_ROOT", tmp_path)
    visible_root = tmp_path / "artifacts" / "tmp" / "asr_events"
    forbidden_root = tmp_path / "configs" / "local"
    visible_root.mkdir(parents=True)
    forbidden_root.mkdir(parents=True)
    target = forbidden_root / "events.json"
    target.write_text(json.dumps(_api_review_events(), ensure_ascii=False), encoding="utf-8")
    link = visible_root / "linked.events.json"
    link.symlink_to(target)

    report = tool.build_copilot_product_value_tri_lane_report_from_relative_paths(
        script_json_path="data/asr_eval/local_samples/private.json",
        mock_events_path="artifacts/tmp/asr_events/linked.events.json",
        real_events_path="outputs/private.events.json",
        real_smoke_report_path="artifacts/tmp/asr_reports/missing.json",
        real_provider="sherpa_onnx_streaming",
    )

    assert report["status"] == "blocked"
    assert "script_json_path is not allowed" in report["validation_errors"]
    assert "mock_events_path resolved path is forbidden: configs/local" in report["validation_errors"]
    assert "real_events_path is not allowed" in report["validation_errors"]
    assert report["safe_to_call_llm_now"] is False
    assert report["safe_to_read_user_audio_now"] is False
    assert report["safe_to_read_configs_local_now"] is False
    assert report["lanes"] == []


def test_tri_lane_gate_cli_smoke_outputs_no_llm_report(tmp_path, monkeypatch, capsys):
    tool = load_tool_module()
    monkeypatch.setattr(tool, "REPO_ROOT", tmp_path)
    script_path = _write_json(
        tmp_path,
        "data/asr_eval/synthetic_meetings/scripts/api-review.json",
        _api_review_script(),
    )
    mock_events_path = _write_json(
        tmp_path,
        "artifacts/tmp/asr_events/api-review-001.mock.events.json",
        _api_review_events(),
    )
    real_events_path = _write_json(
        tmp_path,
        "artifacts/tmp/asr_events/api-review-001.sherpa.events.json",
        _api_review_events(),
    )
    real_smoke_path = _write_json(
        tmp_path,
        "artifacts/tmp/asr_reports/api-review-001.sherpa.smoke-report.json",
        _weak_real_smoke_report(),
    )

    exit_code = tool.main(
        [
            "--script-json",
            script_path.as_posix(),
            "--mock-events",
            mock_events_path.as_posix(),
            "--real-events",
            real_events_path.as_posix(),
            "--real-smoke-report",
            real_smoke_path.as_posix(),
            "--real-provider",
            "sherpa_onnx_streaming",
        ]
    )

    assert exit_code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["report_mode"] == "copilot_product_value_tri_lane_gate"
    assert report["overall_decision"] == "blocked_by_asr_quality"
    assert report["safe_to_call_llm_now"] is False


def test_tri_lane_gate_source_has_no_remote_model_audio_or_process_entrypoints():
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
