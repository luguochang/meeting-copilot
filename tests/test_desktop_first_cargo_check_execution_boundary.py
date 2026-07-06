import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = REPO_ROOT / "code" / "desktop_tauri" / "first-cargo-check-execution.policy.json"
ARTIFACT_POLICY_PATH = REPO_ROOT / "code" / "desktop_tauri" / "cargo-check.policy.json"
PROBE_RESULT_INTAKE_POLICY_PATH = (
    REPO_ROOT / "code" / "desktop_tauri" / "rust-post-install-probe-result-intake.policy.json"
)
TOOL_PATH = REPO_ROOT / "tools" / "desktop_first_cargo_check_execution_boundary.py"

FIRST_CARGO_CHECK_COMMAND = [
    "cargo",
    "check",
    "--manifest-path",
    "code/desktop_tauri/src-tauri/Cargo.toml",
]
REQUIRED_CARGO_ENV = {"CARGO_TARGET_DIR": "artifacts/tmp/desktop_tauri_target"}
ALLOWED_ARTIFACTS = [
    "code/desktop_tauri/src-tauri/Cargo.lock",
    "artifacts/tmp/desktop_tauri_target",
]
REQUIRED_PRECONDITIONS = [
    "pcweb_084_artifact_policy_validation_passed",
    "pcweb_089_toolchain_result_validation_passed",
    "rustc_status_available",
    "cargo_status_available",
    "rustup_status_available",
    "macos_xcode_select_status_available_or_not_applicable",
    "cargo_lock_policy_acknowledged",
    "cargo_target_dir_policy_acknowledged",
    "first_dependency_resolution_network_fetch_approved_or_cache_preseeded",
    "cleanup_policy_acknowledged",
    "explicit_user_approval_for_first_cargo_check",
    "no_audio_worker_secret_remote_boundary_reconfirmed",
]
SAFETY_FLAGS = [
    "safe_to_run_cargo_check_now",
    "safe_to_run_cargo_build_now",
    "safe_to_run_tauri_dev_now",
    "safe_to_run_tauri_build_now",
    "safe_to_spawn_process_now",
    "safe_to_fetch_dependencies_now",
    "safe_to_generate_cargo_lock_now",
    "safe_to_generate_target_dir_now",
    "safe_to_read_configs_local_now",
    "safe_to_read_raw_probe_output_now",
    "safe_to_read_shell_profiles_now",
    "safe_to_read_cargo_home_now",
    "safe_to_read_rustup_home_now",
    "safe_to_request_audio_permission_now",
    "safe_to_capture_audio_now",
    "safe_to_start_asr_worker_now",
    "safe_to_read_provider_config_now",
    "safe_to_read_secret_now",
    "safe_to_call_remote_provider_now",
]
VALID_PROBE_RESULT = {
    "rustc_status": "available",
    "cargo_status": "available",
    "rustup_status": "available",
    "macos_xcode_select_status": "available",
    "macos_xcode_select_path_status": "path_present",
    "first_cargo_check_readiness": "blocked_until_pcweb_084_and_user_approval",
}


def load_tool_module():
    spec = importlib.util.spec_from_file_location(
        "desktop_first_cargo_check_execution_boundary",
        TOOL_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_policy() -> dict:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def load_artifact_policy() -> dict:
    return json.loads(ARTIFACT_POLICY_PATH.read_text(encoding="utf-8"))


def test_first_cargo_check_execution_policy_exists_and_keeps_execution_blocked():
    policy = load_policy()

    assert policy["pcweb_id"] == "PCWEB-090"
    assert policy["policy_name"] == "Desktop First Cargo Check Execution Boundary"
    assert policy["policy_status"] == "first_cargo_check_execution_boundary_policy_only"
    assert policy["default_quality_gate_status"] == "included_in_root_pytest"
    assert policy["execution_boundary_mode"] == "explicit_manual_execution_packet_only"
    assert policy["accepted_artifact_policy_source"] == "pcweb_084_cargo_check_policy_only"
    assert policy["accepted_toolchain_result_source"] == "pcweb_089_normalized_result_only"
    assert policy["cargo_check_execution_status"] == "not_run"
    assert policy["external_command_execution_status"] == "not_run"
    assert policy["approval_status"] == "explicit_user_approval_not_recorded"
    assert policy["first_manual_cargo_check_command"] == FIRST_CARGO_CHECK_COMMAND
    assert policy["first_manual_cargo_check_env"] == REQUIRED_CARGO_ENV
    assert policy["allowed_artifacts_after_explicit_manual_run"] == ALLOWED_ARTIFACTS
    assert policy["required_preconditions_before_manual_execution"] == REQUIRED_PRECONDITIONS
    for flag in SAFETY_FLAGS:
        assert policy[flag] is False


def test_first_cargo_check_execution_tool_source_forbids_command_execution_entrypoints():
    source = TOOL_PATH.read_text(encoding="utf-8")

    assert "subprocess" not in source
    assert "os.system" not in source
    assert "Popen" not in source
    assert "check_call" not in source
    assert "check_output" not in source
    assert "EXTERNAL_COMMAND_EXECUTION_FORBIDDEN = True" in source


def test_report_without_toolchain_result_is_blocked_and_no_command_packet_generated():
    tool = load_tool_module()

    report = tool.build_first_cargo_check_execution_boundary_report(
        policy_path=POLICY_PATH,
        artifact_policy_path=ARTIFACT_POLICY_PATH,
        probe_result_intake_policy_path=PROBE_RESULT_INTAKE_POLICY_PATH,
    )

    assert report["pcweb_id"] == "PCWEB-090"
    assert report["report_mode"] == "first_cargo_check_execution_boundary_static_report"
    assert report["policy_validation_status"] == "passed"
    assert report["artifact_policy_validation_status"] == "passed"
    assert report["toolchain_result_status"] == "missing"
    assert report["execution_packet_status"] == "blocked_by_missing_toolchain_result"
    assert report["cargo_check_execution_status"] == "not_run"
    assert report["external_command_execution_status"] == "not_run"
    assert report["manual_execution_packet"]["packet_status"] == "not_generated"
    for flag in SAFETY_FLAGS:
        assert report[flag] is False


def test_valid_toolchain_result_generates_manual_packet_but_still_does_not_run_cargo():
    tool = load_tool_module()

    report = tool.build_first_cargo_check_execution_boundary_report(
        policy_path=POLICY_PATH,
        artifact_policy_path=ARTIFACT_POLICY_PATH,
        probe_result_intake_policy_path=PROBE_RESULT_INTAKE_POLICY_PATH,
        probe_result=VALID_PROBE_RESULT,
    )

    assert report["policy_validation_status"] == "passed"
    assert report["artifact_policy_validation_status"] == "passed"
    assert report["toolchain_result_status"] == "accepted"
    assert report["execution_packet_status"] == "ready_for_explicit_user_approval"
    assert report["cargo_check_execution_status"] == "not_run"
    assert report["external_command_execution_status"] == "not_run"
    assert report["safe_to_run_cargo_check_now"] is False
    assert report["safe_to_fetch_dependencies_now"] is False
    assert report["safe_to_generate_cargo_lock_now"] is False
    assert report["safe_to_generate_target_dir_now"] is False
    assert report["manual_execution_packet"] == {
        "packet_status": "ready_for_manual_review",
        "command": FIRST_CARGO_CHECK_COMMAND,
        "env": REQUIRED_CARGO_ENV,
        "allowed_artifacts_after_explicit_manual_run": ALLOWED_ARTIFACTS,
        "approval_required": True,
        "command_must_be_run_by": "user_or_separately_approved_runner",
        "post_run_required_action": "record_result_without_raw_output_or_paths",
    }


def test_missing_cargo_status_blocks_manual_packet():
    tool = load_tool_module()
    probe_result = {**VALID_PROBE_RESULT, "cargo_status": "missing"}

    report = tool.build_first_cargo_check_execution_boundary_report(
        policy_path=POLICY_PATH,
        artifact_policy_path=ARTIFACT_POLICY_PATH,
        probe_result_intake_policy_path=PROBE_RESULT_INTAKE_POLICY_PATH,
        probe_result=probe_result,
    )

    assert report["toolchain_result_status"] == "blocked_by_toolchain_result"
    assert "cargo_status must be available" in report["toolchain_result_errors"]
    assert report["execution_packet_status"] == "blocked_by_toolchain_result"
    assert report["manual_execution_packet"]["packet_status"] == "not_generated"
    assert report["safe_to_run_cargo_check_now"] is False


def test_invalid_toolchain_result_does_not_echo_raw_path_or_secret_like_values():
    tool = load_tool_module()
    probe_result = {
        **VALID_PROBE_RESULT,
        "rustc_status": "rustc 1.90.0 /Library/Developer Bearer SECRET_TOKEN_1234567890",
    }

    report = tool.build_first_cargo_check_execution_boundary_report(
        policy_path=POLICY_PATH,
        artifact_policy_path=ARTIFACT_POLICY_PATH,
        probe_result_intake_policy_path=PROBE_RESULT_INTAKE_POLICY_PATH,
        probe_result=probe_result,
    )

    assert report["toolchain_result_status"] == "rejected"
    assert report["execution_packet_status"] == "blocked_by_toolchain_result"
    report_json = json.dumps(report)
    assert "rustc 1.90.0" not in report_json
    assert "/Library/Developer" not in report_json
    assert "Bearer SECRET_TOKEN_1234567890" not in report_json
    assert report["manual_execution_packet"]["packet_status"] == "not_generated"


def test_unknown_toolchain_result_field_name_does_not_echo_raw_path_or_secret_like_values():
    tool = load_tool_module()
    probe_result = {
        **VALID_PROBE_RESULT,
        "/Library/Developer/Bearer SECRET_TOKEN_1234567890": "value",
    }

    report = tool.build_first_cargo_check_execution_boundary_report(
        policy_path=POLICY_PATH,
        artifact_policy_path=ARTIFACT_POLICY_PATH,
        probe_result_intake_policy_path=PROBE_RESULT_INTAKE_POLICY_PATH,
        probe_result=probe_result,
    )

    assert report["toolchain_result_status"] == "rejected"
    assert "unknown result field present" in report["toolchain_result_errors"]
    report_json = json.dumps(report)
    assert "/Library/Developer" not in report_json
    assert "Bearer SECRET_TOKEN_1234567890" not in report_json
    assert "SECRET_TOKEN_1234567890" not in report_json
    assert report["manual_execution_packet"]["packet_status"] == "not_generated"


def test_custom_policy_cannot_relax_identity_command_env_artifacts_preconditions_or_flags(tmp_path):
    tool = load_tool_module()
    custom_policy = load_policy()
    custom_policy["pcweb_id"] = "PCWEB-999"
    custom_policy["policy_name"] = "Untrusted First Cargo Check Policy"
    custom_policy["policy_status"] = "custom_policy_trusted"
    custom_policy["execution_boundary_mode"] = "execute_now"
    custom_policy["first_manual_cargo_check_command"] = ["cargo", "build"]
    custom_policy["first_manual_cargo_check_env"] = {"CARGO_TARGET_DIR": "target"}
    custom_policy["allowed_artifacts_after_explicit_manual_run"] = ["code/desktop_tauri/src-tauri/target"]
    custom_policy["required_preconditions_before_manual_execution"] = []
    for flag in SAFETY_FLAGS:
        custom_policy[flag] = True
    custom_policy_path = tmp_path / "first-cargo-check-execution.policy.json"
    custom_policy_path.write_text(json.dumps(custom_policy), encoding="utf-8")

    report = tool.build_first_cargo_check_execution_boundary_report(
        policy_path=custom_policy_path,
        artifact_policy_path=ARTIFACT_POLICY_PATH,
        probe_result_intake_policy_path=PROBE_RESULT_INTAKE_POLICY_PATH,
        probe_result=VALID_PROBE_RESULT,
    )

    assert report["policy_validation_status"] == "failed"
    assert report["pcweb_id"] == "PCWEB-090"
    assert report["policy_name"] == "Desktop First Cargo Check Execution Boundary"
    assert report["policy_status"] == "first_cargo_check_execution_boundary_policy_only"
    assert set(report["policy_validation_errors"]) >= {
        "pcweb_id must be PCWEB-090",
        "policy_name must be Desktop First Cargo Check Execution Boundary",
        "policy_status must be first_cargo_check_execution_boundary_policy_only",
        "execution_boundary_mode must be explicit_manual_execution_packet_only",
        "first_manual_cargo_check_command must match PCWEB-084 cargo check command",
        "first_manual_cargo_check_env must set CARGO_TARGET_DIR to artifacts/tmp/desktop_tauri_target",
        "allowed_artifacts_after_explicit_manual_run must match PCWEB-084 artifact policy",
        "required_preconditions_before_manual_execution must match PCWEB-090 preconditions",
    }
    assert {f"{flag} must be false" for flag in SAFETY_FLAGS}.issubset(
        set(report["policy_validation_errors"])
    )
    assert report["execution_packet_status"] == "blocked_by_policy_validation"
    assert "cargo build" not in json.dumps(report)


def test_artifact_policy_drift_blocks_manual_packet(tmp_path):
    tool = load_tool_module()
    custom_artifact_policy = load_artifact_policy()
    custom_artifact_policy["future_first_approved_cargo_check"]["command"] = [
        "cargo",
        "check",
    ]
    artifact_policy_path = tmp_path / "cargo-check.policy.json"
    artifact_policy_path.write_text(json.dumps(custom_artifact_policy), encoding="utf-8")

    report = tool.build_first_cargo_check_execution_boundary_report(
        policy_path=POLICY_PATH,
        artifact_policy_path=artifact_policy_path,
        probe_result_intake_policy_path=PROBE_RESULT_INTAKE_POLICY_PATH,
        probe_result=VALID_PROBE_RESULT,
    )

    assert report["artifact_policy_validation_status"] == "failed"
    assert (
        "future_first_approved_cargo_check.command must match the approved cargo check command"
        in report["artifact_policy_validation_errors"]
    )
    assert report["execution_packet_status"] == "blocked_by_artifact_policy_validation"
    assert report["manual_execution_packet"]["packet_status"] == "not_generated"


def test_forbidden_policy_and_result_paths_are_blocked_before_read(tmp_path):
    tool = load_tool_module()
    blocked_policy_path = tmp_path / "CONFIGS" / "LOCAL" / "first-cargo-check-execution.policy.json"
    blocked_policy_path.parent.mkdir(parents=True)
    blocked_policy_path.write_text("{}", encoding="utf-8")

    blocked_policy_report = tool.build_first_cargo_check_execution_boundary_report(
        policy_path=blocked_policy_path,
        artifact_policy_path=ARTIFACT_POLICY_PATH,
        probe_result_intake_policy_path=PROBE_RESULT_INTAKE_POLICY_PATH,
        probe_result=VALID_PROBE_RESULT,
    )

    assert blocked_policy_report["policy_read_status"] == "blocked"
    assert "path is blocked: configs/local" in blocked_policy_report["policy_validation_errors"]
    assert blocked_policy_report["execution_packet_status"] == "blocked_by_path_guard"

    blocked_result_path = tmp_path / "DATA" / "ASR_EVAL" / "SAMPLES" / "probe-result.json"
    blocked_result_report = tool.build_first_cargo_check_execution_boundary_report(
        policy_path=POLICY_PATH,
        artifact_policy_path=ARTIFACT_POLICY_PATH,
        probe_result_intake_policy_path=PROBE_RESULT_INTAKE_POLICY_PATH,
        probe_result_path=blocked_result_path,
    )

    assert blocked_result_report["result_read_status"] == "blocked"
    assert "path is blocked: data/asr_eval/samples" in blocked_result_report[
        "toolchain_result_errors"
    ]
    assert blocked_result_report["execution_packet_status"] == "blocked_by_path_guard"


def test_all_forbidden_paths_are_blocked_for_every_path_input_before_read(tmp_path):
    tool = load_tool_module()
    cases = [
        ("configs/local", ("CONFIGS", "LOCAL")),
        ("data/local_runtime", ("DATA", "LOCAL_RUNTIME")),
        ("outputs", ("OUTPUTS",)),
        ("artifacts/tmp", ("ARTIFACTS", "TMP")),
        ("data/asr_eval/samples", ("DATA", "ASR_EVAL", "SAMPLES")),
    ]

    for expected_label, parts in cases:
        blocked_path = tmp_path.joinpath(*parts, "blocked.json")
        report = tool.build_first_cargo_check_execution_boundary_report(
            policy_path=blocked_path,
            artifact_policy_path=ARTIFACT_POLICY_PATH,
            probe_result_intake_policy_path=PROBE_RESULT_INTAKE_POLICY_PATH,
            probe_result=VALID_PROBE_RESULT,
        )
        assert report["policy_read_status"] == "blocked"
        assert f"path is blocked: {expected_label}" in report["policy_validation_errors"]

        report = tool.build_first_cargo_check_execution_boundary_report(
            policy_path=POLICY_PATH,
            artifact_policy_path=blocked_path,
            probe_result_intake_policy_path=PROBE_RESULT_INTAKE_POLICY_PATH,
            probe_result=VALID_PROBE_RESULT,
        )
        assert report["artifact_policy_read_status"] == "blocked"
        assert f"path is blocked: {expected_label}" in report[
            "artifact_policy_validation_errors"
        ]

        report = tool.build_first_cargo_check_execution_boundary_report(
            policy_path=POLICY_PATH,
            artifact_policy_path=ARTIFACT_POLICY_PATH,
            probe_result_intake_policy_path=blocked_path,
            probe_result=VALID_PROBE_RESULT,
        )
        assert report["result_read_status"] == "blocked"
        assert f"path is blocked: {expected_label}" in report["toolchain_result_errors"]

        report = tool.build_first_cargo_check_execution_boundary_report(
            policy_path=POLICY_PATH,
            artifact_policy_path=ARTIFACT_POLICY_PATH,
            probe_result_intake_policy_path=PROBE_RESULT_INTAKE_POLICY_PATH,
            probe_result_path=blocked_path,
        )
        assert report["result_read_status"] == "blocked"
        assert f"path is blocked: {expected_label}" in report["toolchain_result_errors"]
