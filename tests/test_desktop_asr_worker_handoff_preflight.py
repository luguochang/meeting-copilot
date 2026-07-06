import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DESKTOP_ROOT = REPO_ROOT / "code" / "desktop_tauri"
POLICY_PATH = DESKTOP_ROOT / "asr-worker-handoff-preflight.policy.json"
TOOL_PATH = REPO_ROOT / "tools" / "desktop_asr_worker_handoff_preflight.py"

EXPECTED_SAFETY_FLAGS = [
    "safe_to_spawn_worker_now",
    "safe_to_start_worker_now",
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
    "safe_to_read_event_file_now",
    "safe_to_mutate_web_session_now",
]


def load_tool_module():
    spec = importlib.util.spec_from_file_location(
        "desktop_asr_worker_handoff_preflight",
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
        "session_id": "desktop_worker_handoff_preflight_review",
        "provider": "sherpa_onnx_streaming",
        "event_file_path": "artifacts/tmp/asr_events/desktop-worker-preflight.events.json",
        "source_kind": "preflight_only",
        "chunk_lifecycle": [
            {
                "chunk_id": "chunk_0001",
                "chunk_index": 0,
                "chunk_start_ms": 0,
                "chunk_end_ms": 1600,
                "source_kind": "preflight_only",
            }
        ],
    }


def test_asr_worker_handoff_preflight_policy_exists_and_blocks_side_effects():
    policy = load_policy()

    assert policy["pcweb_id"] == "PCWEB-095"
    assert policy["policy_name"] == "Desktop ASR Worker Handoff Preflight"
    assert policy["policy_status"] == "asr_worker_handoff_preflight_policy_only"
    assert policy["default_quality_gate_status"] == "included_in_root_pytest"
    assert policy["preflight_mode"] == "descriptor_schema_only"
    assert policy["worker_execution_status"] == "not_started"
    assert policy["microphone_capture_status"] == "not_started"
    assert policy["event_file_write_status"] == "not_written"
    assert policy["handoff_api_endpoint"] == "/live/asr/local-event-files/sessions"
    assert policy["allowed_event_file_root"] == "artifacts/tmp/asr_events"
    assert policy["allowed_source_kinds_now"] == ["preflight_only", "synthetic"]
    assert policy["future_source_kinds_requiring_approval"] == ["mic", "file"]
    assert policy["required_descriptor_fields"] == [
        "descriptor_version",
        "session_id",
        "provider",
        "event_file_path",
        "source_kind",
        "chunk_lifecycle",
    ]
    assert policy["required_chunk_lifecycle_fields"] == [
        "chunk_id",
        "chunk_index",
        "chunk_start_ms",
        "chunk_end_ms",
        "source_kind",
    ]
    assert policy["forbidden_roots"] == [
        "configs/local",
        "data/asr_eval/local_samples",
        "data/asr_eval/samples",
        "data/local_runtime",
        "outputs",
    ]
    for flag in EXPECTED_SAFETY_FLAGS:
        assert policy[flag] is False


def test_asr_worker_handoff_preflight_tool_source_forbids_side_effect_entrypoints():
    source = TOOL_PATH.read_text(encoding="utf-8")

    forbidden_snippets = [
        "subprocess",
        "os.system",
        "Popen",
        "check_call",
        "check_output",
        "Command::new",
        "std::process",
        "sounddevice",
        "pyaudio",
        "AVAudioEngine",
        "AudioQueue",
        "write_text",
        "open(",
    ]
    for snippet in forbidden_snippets:
        assert snippet not in source
    assert "EXTERNAL_COMMAND_EXECUTION_FORBIDDEN = True" in source
    assert "FILE_WRITE_FORBIDDEN = True" in source


def test_valid_descriptor_builds_handoff_preview_without_starting_worker():
    tool = load_tool_module()

    report = tool.build_asr_worker_handoff_preflight_report(
        policy_path=POLICY_PATH,
        descriptor=valid_descriptor(),
    )

    assert report["pcweb_id"] == "PCWEB-095"
    assert report["report_mode"] == "desktop_asr_worker_handoff_preflight_static_report"
    assert report["policy_validation_status"] == "passed"
    assert report["descriptor_validation_status"] == "passed"
    assert report["preflight_status"] == "ready_for_web_handoff_contract_review"
    assert report["worker_execution_status"] == "not_started"
    assert report["event_file_write_status"] == "not_written"
    assert report["event_file_read_status"] == "not_read"
    assert report["web_handoff_mutation_status"] == "not_mutated"
    assert report["event_file_path"] == "artifacts/tmp/asr_events/desktop-worker-preflight.events.json"
    assert report["future_web_handoff_request_preview"] == {
        "session_id": "desktop_worker_handoff_preflight_review",
        "provider": "sherpa_onnx_streaming",
        "events_path": "artifacts/tmp/asr_events/desktop-worker-preflight.events.json",
    }
    assert report["next_action"] == "implement_web_handoff_call_after_worker_output_exists"
    for flag in EXPECTED_SAFETY_FLAGS:
        assert report[flag] is False


def test_descriptor_rejects_mic_source_until_audio_adapter_is_approved():
    tool = load_tool_module()
    descriptor = valid_descriptor()
    descriptor["source_kind"] = "mic"
    descriptor["chunk_lifecycle"][0]["source_kind"] = "mic"

    report = tool.build_asr_worker_handoff_preflight_report(
        policy_path=POLICY_PATH,
        descriptor=descriptor,
    )

    assert report["descriptor_validation_status"] == "failed"
    assert report["preflight_status"] == "blocked_by_descriptor_validation"
    assert "source_kind requires later approval: mic" in report["descriptor_validation_errors"]
    assert "chunk_lifecycle[0].source_kind requires later approval: mic" in report[
        "descriptor_validation_errors"
    ]
    assert report["safe_to_capture_audio_now"] is False
    assert report["safe_to_start_worker_now"] is False


def test_descriptor_rejects_forbidden_event_file_paths_and_redacts_invalid_path(tmp_path):
    tool = load_tool_module()
    forbidden_dir = tmp_path / "artifacts" / "tmp" / "asr_events"
    target_dir = tmp_path / "configs" / "local"
    forbidden_dir.mkdir(parents=True)
    target_dir.mkdir(parents=True)
    link = forbidden_dir / "linked.events.json"
    link.symlink_to(target_dir / "events.json")
    descriptor = valid_descriptor()
    descriptor["event_file_path"] = str(link)

    report = tool.build_asr_worker_handoff_preflight_report(
        policy_path=POLICY_PATH,
        descriptor=descriptor,
        repo_root=tmp_path,
    )

    assert report["descriptor_validation_status"] == "failed"
    assert report["event_file_path"] == "<redacted_invalid_path>"
    assert report["descriptor_validation_errors"] == [
        "event_file_path is blocked: configs/local",
    ]
    assert str(link) not in json.dumps(report, ensure_ascii=False)


def test_descriptor_rejects_paths_outside_approved_asr_events_root():
    tool = load_tool_module()
    forbidden_descriptor = valid_descriptor()
    forbidden_descriptor["event_file_path"] = "data/local_runtime/private.events.json"
    outside_descriptor = valid_descriptor()
    outside_descriptor["event_file_path"] = "artifacts/tmp/not_asr_events/private.events.json"

    forbidden_report = tool.build_asr_worker_handoff_preflight_report(
        policy_path=POLICY_PATH,
        descriptor=forbidden_descriptor,
    )
    outside_report = tool.build_asr_worker_handoff_preflight_report(
        policy_path=POLICY_PATH,
        descriptor=outside_descriptor,
    )

    assert forbidden_report["descriptor_validation_status"] == "failed"
    assert forbidden_report["event_file_path"] == "<redacted_invalid_path>"
    assert forbidden_report["descriptor_validation_errors"] == [
        "event_file_path is blocked: data/local_runtime",
    ]
    assert outside_report["descriptor_validation_status"] == "failed"
    assert outside_report["event_file_path"] == "<redacted_invalid_path>"
    assert "event_file_path is not under approved ASR events root" in outside_report[
        "descriptor_validation_errors"
    ]


def test_descriptor_rejects_invalid_chunk_lifecycle():
    tool = load_tool_module()
    descriptor = valid_descriptor()
    descriptor["chunk_lifecycle"] = [
        {
            "chunk_id": "chunk_0001",
            "chunk_index": -1,
            "chunk_start_ms": 2000,
            "chunk_end_ms": 1000,
            "source_kind": "synthetic",
        }
    ]

    report = tool.build_asr_worker_handoff_preflight_report(
        policy_path=POLICY_PATH,
        descriptor=descriptor,
    )

    assert report["descriptor_validation_status"] == "failed"
    assert "chunk_lifecycle[0].chunk_index must be a non-negative integer" in report[
        "descriptor_validation_errors"
    ]
    assert "chunk_lifecycle[0].chunk_end_ms must be greater than or equal to chunk_start_ms" in report[
        "descriptor_validation_errors"
    ]
    assert report["safe_to_write_event_file_now"] is False


def test_custom_policy_cannot_enable_worker_or_audio_side_effects(tmp_path):
    tool = load_tool_module()
    policy = load_policy()
    policy["safe_to_start_worker_now"] = True
    policy["safe_to_capture_audio_now"] = True
    policy_path = tmp_path / "asr-worker-handoff-preflight.policy.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")

    report = tool.build_asr_worker_handoff_preflight_report(
        policy_path=policy_path,
        descriptor=valid_descriptor(),
    )

    assert report["policy_validation_status"] == "failed"
    assert "safe_to_start_worker_now must be false" in report["policy_validation_errors"]
    assert "safe_to_capture_audio_now must be false" in report["policy_validation_errors"]
    assert report["preflight_status"] == "blocked_by_policy_validation"
    assert report["safe_to_start_worker_now"] is False
    assert report["safe_to_capture_audio_now"] is False
