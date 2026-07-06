import importlib.util
import io
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "funasr_synthetic_smoke_batch_evidence_assembler.py"
PACKET_TOOL_PATH = REPO_ROOT / "tools" / "funasr_synthetic_smoke_execution_packet.py"
SINGLE_RESULT_BUILDER_PATH = REPO_ROOT / "tools" / "funasr_synthetic_smoke_single_result_builder.py"


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
    "safe_to_write_artifacts_now",
]


SCENARIOS = [
    ("api-review-001", "engineering"),
    ("architecture-review-001", "engineering"),
    ("incident-review-001", "engineering"),
    ("release-review-001", "engineering"),
    ("non-engineering-control-001", "negative_control"),
]


def load_tool_module():
    spec = importlib.util.spec_from_file_location(
        "funasr_synthetic_smoke_batch_evidence_assembler",
        TOOL_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def ready_readiness_report() -> dict:
    return {
        "report_mode": "funasr_synthetic_smoke_readiness",
        "report_version": "funasr_synthetic_smoke_readiness.v1",
        "readiness_status": "cache_preflight_passed_offline_execution_not_proven",
        "provider": "funasr_streaming",
        "model_alias": "paraformer-zh-streaming",
        "device": "cpu",
        "venv_python": "code/asr_runtime/.venv-funasr/bin/python",
        "funasr_script": "code/asr_runtime/scripts/transcribe_funasr.py",
        "local_model_dir_label": (
            "modelscope_runtime_models_iic/"
            "speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online"
        ),
        "required_cached_models_status": "present",
        "offline_guard_status": "required_before_execution",
        "model_download_status": "not_started",
        "execution_mode": "preflight_only_no_execution_authorization",
        "safe_to_execute_local_funasr_now": False,
        "safe_to_download_models": False,
        "safe_to_read_user_audio": False,
        "safe_to_read_configs_local": False,
        "safe_to_call_remote_asr": False,
        "safe_to_call_llm": False,
        "validation_errors": [],
    }


def engineering_result(scenario_id: str) -> dict:
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
            "first_partial_latency_seconds_p95": 1.2,
            "final_latency_seconds_p95": 6.5,
            "suggestion_candidate_latency_seconds_p95": 19.0,
        },
        "asr_metrics": {"rtf": 0.31},
        "technical_entity_metrics": {
            "raw_recall": 0.82,
            "normalized_recall": 0.87,
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


def single_smoke_report(scenario_id: str, scenario_kind: str) -> dict:
    scenario_result = (
        negative_control_result()
        if scenario_kind == "negative_control"
        else engineering_result(scenario_id)
    )
    return {
        "manifest_version": "funasr_synthetic_smoke_result.v1",
        "evidence_kind": "single_synthetic_smoke",
        "provider": "funasr_streaming",
        "model_alias": "paraformer-zh-streaming",
        "source_boundary": "synthetic_audio_no_user_audio",
        "scenario_results": [scenario_result],
    }


def drv045_packet() -> dict:
    smoke_paths = [
        f"artifacts/tmp/asr_reports/{scenario_id}.funasr.smoke-report.json"
        for scenario_id, _kind in SCENARIOS
    ]
    return {
        "decision_id": "DRV-045",
        "packet_mode": "funasr_synthetic_smoke_execution_packet",
        "packet_version": "funasr_synthetic_smoke_execution_packet.v1",
        "packet_status": "ready_for_manual_batch_funasr_synthetic_smoke_run",
        "execution_approval_status": "not_approved_manual_run_only",
        "provider": "funasr_streaming",
        "model_alias": "paraformer-zh-streaming",
        "scenario_count": 5,
        "engineering_scenario_count": 4,
        "negative_control_count": 1,
        "expected_outputs": {
            "smoke_report_paths": smoke_paths,
        },
        "expected_drv044_batch_artifact_provenance": {
            "source_kind": "local_funasr_synthetic_smoke_artifacts",
            "artifacts": [
                {
                    "artifact_kind": "funasr_synthetic_smoke_result_report",
                    "scenario_id": scenario_id,
                    "path": path,
                    "sha256_source": "compute_after_manual_run",
                }
                for (scenario_id, _kind), path in zip(SCENARIOS, smoke_paths)
            ],
        },
        "safe_to_execute_now": False,
        "safe_to_run_asr_now": False,
        "safe_to_download_models_now": False,
        "safe_to_capture_microphone_now": False,
        "safe_to_read_user_audio_now": False,
        "safe_to_read_configs_local_now": False,
        "safe_to_call_remote_asr_now": False,
        "safe_to_call_llm_now": False,
        "safe_to_download_public_audio_now": False,
        "safe_to_read_audio_file_now": False,
        "safe_to_write_artifacts_now": False,
        "validation_errors": [],
    }


def write_smoke_reports(repo_root: Path) -> None:
    report_root = repo_root / "artifacts" / "tmp" / "asr_reports"
    report_root.mkdir(parents=True, exist_ok=True)
    for scenario_id, scenario_kind in SCENARIOS:
        path = report_root / f"{scenario_id}.funasr.smoke-report.json"
        path.write_text(
            json.dumps(single_smoke_report(scenario_id, scenario_kind), sort_keys=True),
            encoding="utf-8",
        )


def write_builder_inputs(repo_root: Path, packet: dict) -> None:
    for preview in packet["postprocess_command_previews"]:
        scenario_id = preview["scenario_id"]
        script_json_path = preview["smoke_report_argv"][
            preview["smoke_report_argv"].index("--script-json") + 1
        ]
        provider_json_path = preview["smoke_report_argv"][
            preview["smoke_report_argv"].index("--provider-json") + 1
        ]
        transcript_report_path = preview["smoke_report_argv"][
            preview["smoke_report_argv"].index("--transcript-report") + 1
        ]
        events_json_path = preview["smoke_report_argv"][
            preview["smoke_report_argv"].index("--events-json") + 1
        ]
        is_negative_control = scenario_id == "non-engineering-control-001"
        technical_entities = [] if is_negative_control else ["payment-gateway", "request_id", "40012", "P99"]
        text = (
            "我们先看 payment-gateway 的 request_id 兼容，错误码 40012，灰度看 P99。"
            if not is_negative_control
            else "我们今天确认团建时间，周五下午大家是否方便。"
        )
        script = {
            "script_id": scenario_id,
            "scenario": "non_engineering_control" if is_negative_control else "api_review",
            "technical_entities": technical_entities,
            "expected_state_events": [] if is_negative_control else [{"event_type": "risk.created"}],
            "expected_suggestion_cards": []
            if is_negative_control
            else [{"card_id": f"{scenario_id}-card", "should_show": True, "evidence_span_required": True}],
        }
        provider = {
            "status": "ok",
            "text": text,
            "latency_ms": 1200,
            "audio_duration_seconds": 12.0,
            "rtf": 0.1,
            "segments": [{"id": "funasr_001", "start_ms": 0, "end_ms": 12000, "text": text}],
        }
        transcript = {
            "normalized_text": text,
            "rtf": 0.1,
            "evidence_spans": [
                {"id": "ev_001", "segment_id": "seg_001", "start_ms": 0, "end_ms": 12000, "quote": text}
            ],
        }
        events = [
            {
                "event_type": "partial",
                "segment_id": "seg_001",
                "text": text[:12],
                "start_ms": 0,
                "end_ms": 1200,
                "received_at_ms": 900,
            },
            {
                "event_type": "final",
                "segment_id": "seg_001",
                "text": text,
                "start_ms": 0,
                "end_ms": 12000,
                "received_at_ms": 13200,
            },
            {"event_type": "end_of_stream", "received_at_ms": 13250},
        ]
        for relative_path, payload in [
            (script_json_path, script),
            (provider_json_path, provider),
            (transcript_report_path, transcript),
            (events_json_path, events),
        ]:
            path = repo_root / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def run_packet_smoke_report_commands(repo_root: Path, packet: dict, builder) -> None:
    for preview in packet["postprocess_command_previews"]:
        output_path = repo_root / preview["smoke_report_stdout_redirect_path"]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        out = io.StringIO()
        exit_code = builder.main(preview["smoke_report_argv"][2:], out=out)
        assert exit_code == 0
        output_path.write_text(out.getvalue(), encoding="utf-8")


def test_default_assembler_blocks_without_execution_packet():
    tool = load_tool_module()

    report = tool.build_funasr_synthetic_smoke_batch_evidence_assembly()

    assert report["decision_id"] == "DRV-046"
    assert report["assembly_mode"] == "funasr_synthetic_smoke_batch_evidence_assembler"
    assert report["assembly_status"] == "blocked_missing_drv045_execution_packet"
    assert report["drv044_gate_report"] is None
    assert report["assembled_evidence_report"] is None
    assert report["next_action"] == "provide_drv045_execution_packet_after_manual_smoke_run"
    for flag in EXPECTED_FALSE_FLAGS:
        assert report[flag] is False


def test_execution_packet_smoke_builder_outputs_feed_batch_assembler(tmp_path, monkeypatch):
    packet_tool = load_module(PACKET_TOOL_PATH, "funasr_synthetic_smoke_execution_packet")
    builder = load_module(SINGLE_RESULT_BUILDER_PATH, "funasr_synthetic_smoke_single_result_builder")
    assembler = load_tool_module()
    repo_root = tmp_path / "repo"
    monkeypatch.setattr(builder, "REPO_ROOT", repo_root)
    monkeypatch.setattr(assembler, "REPO_ROOT", repo_root)
    packet = packet_tool.build_funasr_synthetic_smoke_execution_packet(
        funasr_readiness_report=ready_readiness_report(),
    )
    write_builder_inputs(repo_root, packet)

    run_packet_smoke_report_commands(repo_root, packet, builder)
    report = assembler.build_funasr_synthetic_smoke_batch_evidence_assembly(
        execution_packet=packet,
    )

    assert report["assembly_status"] == "drv044_batch_evidence_validated"
    assert report["artifact_read_status"] == "read"
    assert report["artifact_count"] == 5
    assert report["drv044_gate_report"]["quality_evidence_status"] == (
        "funasr_synthetic_smoke_quality_batch_confirmed"
    )
    assert report["counts_as_asr_quality_go_evidence"] is True
    assert report["counts_as_real_mic_go_evidence"] is False


def test_assembler_reads_approved_smoke_reports_computes_sha256_and_passes_drv044(tmp_path, monkeypatch):
    tool = load_tool_module()
    repo_root = tmp_path / "repo"
    monkeypatch.setattr(tool, "REPO_ROOT", repo_root)
    write_smoke_reports(repo_root)

    report = tool.build_funasr_synthetic_smoke_batch_evidence_assembly(
        execution_packet=drv045_packet(),
    )

    assert report["assembly_status"] == "drv044_batch_evidence_validated"
    assert report["artifact_read_status"] == "read"
    assert report["artifact_count"] == 5
    assert report["assembled_evidence_report"]["evidence_kind"] == "batch_synthetic_confirmation"
    assert len(report["assembled_evidence_report"]["scenario_results"]) == 5
    provenance = report["assembled_evidence_report"]["batch_artifact_provenance"]
    assert provenance["source_kind"] == "local_funasr_synthetic_smoke_artifacts"
    assert len(provenance["artifacts"]) == 5
    assert len(provenance["artifacts"][0]["sha256"]) == 64
    assert report["drv044_gate_report"]["quality_evidence_status"] == (
        "funasr_synthetic_smoke_quality_batch_confirmed"
    )
    assert report["drv044_gate_report"]["batch_artifact_provenance_status"] == "validated"
    assert report["counts_as_asr_quality_go_evidence"] is True
    assert report["counts_as_real_mic_go_evidence"] is False
    for flag in EXPECTED_FALSE_FLAGS:
        assert report[flag] is False
    report_json = json.dumps(report, ensure_ascii=False)
    assert str(tmp_path) not in report_json
    assert "/Users/" not in report_json
    assert "configs/local" not in report_json


def test_assembler_blocks_missing_manual_smoke_artifacts(tmp_path, monkeypatch):
    tool = load_tool_module()
    repo_root = tmp_path / "repo"
    monkeypatch.setattr(tool, "REPO_ROOT", repo_root)

    report = tool.build_funasr_synthetic_smoke_batch_evidence_assembly(
        execution_packet=drv045_packet(),
    )

    assert report["assembly_status"] == "blocked_missing_manual_smoke_artifacts"
    assert "manual smoke artifact missing: artifacts/tmp/asr_reports/api-review-001.funasr.smoke-report.json" in report[
        "validation_errors"
    ]
    assert report["drv044_gate_report"] is None
    assert report["counts_as_asr_quality_go_evidence"] is False
    assert report["safe_to_read_audio_file_now"] is False


def test_assembler_blocks_forbidden_packet_path_before_reading(monkeypatch):
    tool = load_tool_module()

    def fail_if_read(*args, **kwargs):
        raise AssertionError("packet path was read before guard")

    monkeypatch.setattr(Path, "read_text", fail_if_read)

    report = tool.build_funasr_synthetic_smoke_batch_evidence_assembly(
        execution_packet_path="configs/local/private-packet.json",
    )

    assert report["assembly_status"] == "blocked_by_packet_path_guard"
    assert report["execution_packet_read_status"] == "blocked"
    assert report["validation_errors"] == ["execution_packet_path is blocked: configs/local"]
    assert report["safe_to_read_configs_local_now"] is False


def test_assembler_blocks_packet_with_unsafe_artifact_path():
    tool = load_tool_module()
    packet = drv045_packet()
    packet["expected_drv044_batch_artifact_provenance"]["artifacts"][0]["path"] = (
        "outputs/private-smoke.json"
    )

    report = tool.build_funasr_synthetic_smoke_batch_evidence_assembly(
        execution_packet=packet,
    )

    assert report["assembly_status"] == "blocked_invalid_drv045_execution_packet"
    assert "artifact path is not under approved root: artifacts/tmp/asr_reports" in report[
        "validation_errors"
    ]
    assert report["drv044_gate_report"] is None
    assert report["counts_as_asr_quality_go_evidence"] is False


def test_assembler_surfaces_drv044_quality_blockers_without_claiming_go(tmp_path, monkeypatch):
    tool = load_tool_module()
    repo_root = tmp_path / "repo"
    monkeypatch.setattr(tool, "REPO_ROOT", repo_root)
    write_smoke_reports(repo_root)
    bad_report_path = repo_root / "artifacts" / "tmp" / "asr_reports" / "api-review-001.funasr.smoke-report.json"
    bad_report = single_smoke_report("api-review-001", "engineering")
    bad_report["scenario_results"][0]["safety"]["called_remote_asr"] = True
    bad_report_path.write_text(json.dumps(bad_report, sort_keys=True), encoding="utf-8")

    report = tool.build_funasr_synthetic_smoke_batch_evidence_assembly(
        execution_packet=drv045_packet(),
    )

    assert report["assembly_status"] == "drv044_batch_evidence_blocked"
    assert report["drv044_gate_report"]["quality_evidence_status"] == "blocked"
    assert "called_remote_asr must be false" in report["drv044_gate_report"]["validation_errors"]
    assert report["counts_as_asr_quality_go_evidence"] is False
    assert report["safe_to_call_remote_asr_now"] is False
