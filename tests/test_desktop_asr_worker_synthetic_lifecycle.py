import importlib.util
import io
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DESKTOP_ROOT = REPO_ROOT / "code" / "desktop_tauri"
POLICY_PATH = DESKTOP_ROOT / "asr-worker-synthetic-lifecycle.policy.json"
TOOL_PATH = REPO_ROOT / "tools" / "desktop_asr_worker_synthetic_lifecycle.py"

EXPECTED_FALSE_FLAGS = [
    "safe_to_spawn_worker_now",
    "safe_to_start_real_worker_now",
    "safe_to_capture_audio_now",
    "safe_to_request_audio_permission_now",
    "safe_to_read_user_audio_now",
    "safe_to_read_configs_local_now",
    "safe_to_read_secret_now",
    "safe_to_call_remote_asr_now",
    "safe_to_call_llm_now",
    "safe_to_download_models_now",
    "safe_to_write_runtime_audio_now",
    "safe_to_write_event_file_now",
    "safe_to_mutate_production_web_session_now",
    "safe_to_run_tauri_or_cargo_now",
]

EXPECTED_COMMAND_SEQUENCE = [
    "worker.prepare",
    "worker.start",
    "worker.collect_events",
    "worker.stop",
    "worker.cleanup",
]


def load_tool_module():
    spec = importlib.util.spec_from_file_location(
        "desktop_asr_worker_synthetic_lifecycle",
        TOOL_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_policy() -> dict:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def valid_descriptor() -> dict:
    return {
        "descriptor_version": "desktop_asr_worker_handoff_preflight.v1",
        "session_id": "desktop_worker_synthetic_lifecycle",
        "provider": "sherpa_onnx_streaming",
        "event_file_path": "artifacts/tmp/asr_events/desktop-worker-synthetic-lifecycle.events.json",
        "source_kind": "synthetic",
        "chunk_lifecycle": [
            {
                "chunk_id": "chunk_0001",
                "chunk_index": 0,
                "chunk_start_ms": 0,
                "chunk_end_ms": 2400,
                "source_kind": "synthetic",
            }
        ],
    }


def command_request(
    *,
    command_id: str,
    current_state: str,
    requested_state_after: str,
    source_kind: str = "synthetic",
) -> dict:
    return {
        "protocol_version": "desktop_asr_worker_command_protocol.v1",
        "command_id": command_id,
        "request_id": command_id.replace(".", "_"),
        "session_id": "desktop_worker_synthetic_lifecycle",
        "worker_id": "desktop_worker_synthetic_lifecycle_worker",
        "source_kind": source_kind,
        "current_state": current_state,
        "requested_state_after": requested_state_after,
        "event_output_path": "artifacts/tmp/asr_events/desktop-worker-synthetic-lifecycle.events.json",
        "runtime_root": "artifacts/tmp/desktop_asr_worker_runtime/desktop-worker-synthetic-lifecycle",
    }


def valid_command_requests() -> list[dict]:
    return [
        command_request(
            command_id="worker.prepare",
            current_state="not_prepared",
            requested_state_after="prepared",
        ),
        command_request(
            command_id="worker.start",
            current_state="prepared",
            requested_state_after="running",
        ),
        command_request(
            command_id="worker.collect_events",
            current_state="running",
            requested_state_after="unchanged",
        ),
        command_request(
            command_id="worker.stop",
            current_state="running",
            requested_state_after="stopped",
        ),
        command_request(
            command_id="worker.cleanup",
            current_state="stopped",
            requested_state_after="cleaned",
        ),
    ]


def minimal_asr_events() -> list[dict]:
    return [
        {
            "event_type": "final",
            "segment_id": "lifecycle_seg_001",
            "text": "API 回滚负责人还没有确认，P99 监控也要补。",
            "start_ms": 0,
            "end_ms": 2400,
            "received_at_ms": 2500,
            "confidence": 0.91,
        },
        {
            "event_type": "end_of_stream",
            "segment_id": "lifecycle_eos",
            "text": "",
            "start_ms": 2400,
            "end_ms": 2500,
            "received_at_ms": 2500,
        },
    ]


def write_event_file(tmp_path: Path, descriptor: dict) -> None:
    event_file = tmp_path / descriptor["event_file_path"]
    event_file.parent.mkdir(parents=True)
    event_file.write_text(json.dumps(minimal_asr_events(), ensure_ascii=False), encoding="utf-8")


def test_synthetic_lifecycle_policy_exists_and_blocks_real_worker_effects():
    policy = load_policy()

    assert policy["pcweb_id"] == "PCWEB-100"
    assert policy["policy_name"] == "Desktop ASR Worker Synthetic Lifecycle Harness"
    assert policy["policy_status"] == "desktop_asr_worker_synthetic_lifecycle_policy_only"
    assert policy["default_quality_gate_status"] == "included_in_root_pytest"
    assert policy["required_previous_contracts"] == ["PCWEB-096", "PCWEB-099"]
    assert policy["lifecycle_mode"] == "synthetic_event_file_lifecycle_only"
    assert policy["default_mode"] == "synthetic_lifecycle_test"
    assert policy["synthetic_lifecycle_test_status"] == "explicit_mode_only"
    assert policy["approved_event_file_root"] == "artifacts/tmp/asr_events"
    assert policy["approved_data_dir_root"] == "artifacts/tmp/desktop_handoff_dry_run"
    assert policy["approved_runtime_root"] == "artifacts/tmp/desktop_asr_worker_runtime"
    assert policy["required_command_sequence"] == EXPECTED_COMMAND_SEQUENCE
    assert policy["allowed_source_kinds_now"] == ["synthetic"]
    for flag in EXPECTED_FALSE_FLAGS:
        assert policy[flag] is False


def test_tool_source_does_not_spawn_processes_or_access_audio_or_models():
    source = TOOL_PATH.read_text(encoding="utf-8")

    forbidden_snippets = [
        "import subprocess",
        "os.system",
        "Popen",
        "check_call",
        "check_output",
        "sounddevice",
        "pyaudio",
        "AVAudioEngine",
        "AudioQueue",
        "modelscope",
        "AutoModel",
    ]
    for snippet in forbidden_snippets:
        assert snippet not in source
    assert "EXTERNAL_COMMAND_EXECUTION_FORBIDDEN = True" in source


def test_synthetic_lifecycle_runs_command_sequence_and_temp_web_handoff(tmp_path):
    tool = load_tool_module()
    descriptor = valid_descriptor()
    write_event_file(tmp_path, descriptor)

    report = tool.build_desktop_asr_worker_synthetic_lifecycle_report(
        policy_path=POLICY_PATH,
        descriptor=descriptor,
        command_requests=valid_command_requests(),
        repo_root=tmp_path,
        data_dir=tmp_path / "artifacts" / "tmp" / "desktop_handoff_dry_run" / "lifecycle-data",
    )

    assert report["pcweb_id"] == "PCWEB-100"
    assert report["lifecycle_harness_status"] == "synthetic_lifecycle_completed"
    assert report["command_protocol_validation_status"] == "passed"
    assert report["synthetic_handoff_status"] == "synthetic_web_handoff_passed"
    assert report["final_worker_state"] == "cleaned"
    assert report["command_sequence_observed"] == EXPECTED_COMMAND_SEQUENCE
    assert [step["state_before"] for step in report["state_timeline"]] == [
        "not_prepared",
        "prepared",
        "running",
        "running",
        "stopped",
    ]
    assert [step["state_after"] for step in report["state_timeline"]] == [
        "prepared",
        "running",
        "running",
        "stopped",
        "cleaned",
    ]
    assert report["web_handoff_response_summary"] == {
        "session_id": "desktop_worker_synthetic_lifecycle",
        "ingest_mode": "local_asr_event_file",
        "event_source_provider": "sherpa_onnx_streaming",
        "transcript_final_count": 1,
        "evidence_span_count": 1,
        "state_event_count": 1,
        "scheduler_event_count": 1,
        "suggestion_candidate_count": 1,
        "llm_request_draft_count": 1,
        "suggestion_card_count": 0,
        "all_llm_statuses": ["not_called"],
        "worker_to_web_live_session_closure_status": "closed_to_evidence_state_gap",
    }
    assert report["safe_to_read_approved_asr_event_file_now"] is True
    assert report["safe_to_mutate_temp_web_session_now"] is True
    for flag in EXPECTED_FALSE_FLAGS:
        assert report[flag] is False


def test_synthetic_lifecycle_rejects_mic_source_before_event_file_read(tmp_path):
    tool = load_tool_module()
    descriptor = valid_descriptor()
    descriptor["source_kind"] = "mic"
    descriptor["chunk_lifecycle"][0]["source_kind"] = "mic"
    requests = [
        command_request(
            command_id=request["command_id"],
            current_state=request["current_state"],
            requested_state_after=request["requested_state_after"],
            source_kind="mic",
        )
        for request in valid_command_requests()
    ]

    report = tool.build_desktop_asr_worker_synthetic_lifecycle_report(
        policy_path=POLICY_PATH,
        descriptor=descriptor,
        command_requests=requests,
        repo_root=tmp_path,
        data_dir=tmp_path / "artifacts" / "tmp" / "desktop_handoff_dry_run" / "lifecycle-data",
    )

    assert report["lifecycle_harness_status"] == "blocked_by_command_protocol"
    assert report["synthetic_handoff_status"] == "not_started"
    assert report["event_file_read_status"] == "not_read"
    assert report["web_handoff_mutation_status"] == "not_mutated"
    assert any("source_kind requires later approval: mic" in error for error in report["errors"])
    assert report["safe_to_read_approved_asr_event_file_now"] is False
    assert report["safe_to_mutate_temp_web_session_now"] is False


def test_synthetic_lifecycle_rejects_command_order_without_event_file_read(tmp_path):
    tool = load_tool_module()
    descriptor = valid_descriptor()
    requests = valid_command_requests()
    requests[0], requests[1] = requests[1], requests[0]

    report = tool.build_desktop_asr_worker_synthetic_lifecycle_report(
        policy_path=POLICY_PATH,
        descriptor=descriptor,
        command_requests=requests,
        repo_root=tmp_path,
        data_dir=tmp_path / "artifacts" / "tmp" / "desktop_handoff_dry_run" / "lifecycle-data",
    )

    assert report["lifecycle_harness_status"] == "blocked_by_lifecycle_sequence"
    assert report["synthetic_handoff_status"] == "not_started"
    assert report["event_file_read_status"] == "not_read"
    assert "command sequence must be worker.prepare -> worker.start -> worker.collect_events -> worker.stop -> worker.cleanup" in report["errors"]


def test_synthetic_lifecycle_blocks_missing_event_file_without_success_flags(tmp_path):
    tool = load_tool_module()
    descriptor = valid_descriptor()

    report = tool.build_desktop_asr_worker_synthetic_lifecycle_report(
        policy_path=POLICY_PATH,
        descriptor=descriptor,
        command_requests=valid_command_requests(),
        repo_root=tmp_path,
        data_dir=tmp_path / "artifacts" / "tmp" / "desktop_handoff_dry_run" / "lifecycle-data",
    )

    assert report["lifecycle_harness_status"] == "blocked_by_synthetic_handoff"
    assert report["synthetic_handoff_status"] == "blocked_by_web_handoff_response"
    assert report["event_file_read_status"] == "not_read"
    assert report["web_handoff_mutation_status"] == "not_mutated"
    assert report["final_worker_state"] == "running"
    assert report["safe_to_read_approved_asr_event_file_now"] is False
    assert report["safe_to_mutate_temp_web_session_now"] is False


def test_custom_policy_cannot_enable_real_worker_audio_remote_or_model_download(tmp_path):
    tool = load_tool_module()
    descriptor = valid_descriptor()
    policy = load_policy()
    policy["safe_to_spawn_worker_now"] = True
    policy["safe_to_capture_audio_now"] = True
    policy["safe_to_call_remote_asr_now"] = True
    policy["safe_to_download_models_now"] = True
    policy_path = tmp_path / "asr-worker-synthetic-lifecycle.policy.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")

    report = tool.build_desktop_asr_worker_synthetic_lifecycle_report(
        policy_path=policy_path,
        descriptor=descriptor,
        command_requests=valid_command_requests(),
        repo_root=tmp_path,
        data_dir=tmp_path / "artifacts" / "tmp" / "desktop_handoff_dry_run" / "lifecycle-data",
    )

    assert report["policy_validation_status"] == "failed"
    assert report["lifecycle_harness_status"] == "blocked_by_policy_validation"
    assert "safe_to_spawn_worker_now must be false" in report["policy_validation_errors"]
    assert "safe_to_capture_audio_now must be false" in report["policy_validation_errors"]
    assert "safe_to_call_remote_asr_now must be false" in report["policy_validation_errors"]
    assert "safe_to_download_models_now must be false" in report["policy_validation_errors"]
    assert report["event_file_read_status"] == "not_read"
    assert report["web_handoff_mutation_status"] == "not_mutated"


def test_cli_returns_nonzero_for_blocked_synthetic_lifecycle():
    tool = load_tool_module()
    output = io.StringIO()

    exit_code = tool.main(
        [
            "--descriptor-json",
            json.dumps(valid_descriptor()),
            "--command-requests-json",
            json.dumps(valid_command_requests()),
        ],
        out=output,
    )
    report = json.loads(output.getvalue())

    assert exit_code == 1
    assert report["lifecycle_harness_status"] == "blocked_by_synthetic_handoff"
