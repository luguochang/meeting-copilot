import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = REPO_ROOT / "code" / "desktop_tauri" / "rust-post-install-probe-result-intake.policy.json"
TOOL_PATH = REPO_ROOT / "tools" / "desktop_rust_post_install_probe_result_intake.py"

SAFETY_FLAGS = [
    "safe_to_run_post_install_probe_now",
    "safe_to_run_rustc_version_now",
    "safe_to_run_cargo_version_now",
    "safe_to_run_rustup_version_now",
    "safe_to_run_xcode_select_probe_now",
    "safe_to_run_cargo_check_now",
    "safe_to_run_cargo_build_now",
    "safe_to_run_tauri_dev_now",
    "safe_to_run_tauri_build_now",
    "safe_to_fetch_dependencies_now",
    "safe_to_generate_cargo_lock_now",
    "safe_to_generate_target_dir_now",
    "safe_to_read_configs_local_now",
    "safe_to_read_raw_probe_output_now",
    "safe_to_read_shell_profiles_now",
    "safe_to_read_cargo_home_now",
    "safe_to_read_rustup_home_now",
    "safe_to_accept_raw_probe_output_now",
]

ALLOWED_RESULT_FIELDS = [
    "rustc_status",
    "cargo_status",
    "rustup_status",
    "macos_xcode_select_status",
    "macos_xcode_select_path_status",
    "first_cargo_check_readiness",
]

ALLOWED_STATUS_VALUES = {
    "rustc_status": ["available", "missing", "unexpected_error", "not_run"],
    "cargo_status": ["available", "missing", "unexpected_error", "not_run"],
    "rustup_status": ["available", "missing", "unexpected_error", "not_run"],
    "macos_xcode_select_status": [
        "available",
        "missing",
        "unexpected_error",
        "not_run",
        "not_applicable",
    ],
    "macos_xcode_select_path_status": [
        "path_present",
        "path_missing",
        "not_applicable",
        "not_run",
    ],
    "first_cargo_check_readiness": ["blocked_until_pcweb_084_and_user_approval"],
}

FORBIDDEN_RAW_RESULT_FIELDS = [
    "stdout",
    "stderr",
    "raw_stdout",
    "raw_stderr",
    "command",
    "command_text",
    "executable_path",
    "xcode_path",
    "developer_tools_path",
    "path",
    "env",
    "cwd",
    "shell_profile",
    "path_environment",
    "cargo_home",
    "rustup_home",
    "dependency_cache_path",
    "target_dir",
    "cargo_lock_path",
    "provider_config",
    "api_key",
    "authorization",
    "bearer_token",
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
        "desktop_rust_post_install_probe_result_intake",
        TOOL_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_policy() -> dict:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def test_probe_result_intake_policy_exists_and_blocks_execution():
    policy = load_policy()

    assert policy["pcweb_id"] == "PCWEB-089"
    assert policy["policy_name"] == "Desktop Rust Post-Install Probe Result Intake Policy"
    assert policy["policy_status"] == "rust_post_install_probe_result_intake_policy_only"
    assert policy["default_quality_gate_status"] == "included_in_root_pytest"
    assert policy["result_intake_mode"] == "manual_result_validation_only"
    assert policy["accepted_result_source"] == "caller_provided_json_only"
    assert policy["probe_execution_status"] == "not_run"
    assert policy["external_command_execution_status"] == "not_run"
    assert policy["cargo_check_readiness"] == "blocked_until_pcweb_084_and_user_approval"
    for field in SAFETY_FLAGS:
        assert policy[field] is False


def test_policy_records_exact_result_schema_status_values_and_raw_field_blocks():
    policy = load_policy()

    assert policy["allowed_result_fields"] == ALLOWED_RESULT_FIELDS
    assert policy["allowed_status_values"] == ALLOWED_STATUS_VALUES
    assert policy["forbidden_raw_result_fields"] == FORBIDDEN_RAW_RESULT_FIELDS
    assert "explicit_first_cargo_check_approval_still_required" in policy[
        "next_required_decisions"
    ]
    assert "pcweb_084_artifact_policy_reacknowledged" in policy["cargo_check_blockers"]
    assert "explicit_user_approval_for_first_cargo_check" in policy["cargo_check_blockers"]


def test_result_intake_report_without_result_is_no_command_no_result():
    tool = load_tool_module()

    report = tool.build_rust_post_install_probe_result_intake_report(policy_path=POLICY_PATH)

    assert report["pcweb_id"] == "PCWEB-089"
    assert report["report_mode"] == "rust_post_install_probe_result_intake_static_report"
    assert report["policy_validation_status"] == "passed"
    assert report["result_intake_mode"] == "manual_result_validation_only"
    assert report["result_validation_status"] == "not_provided"
    assert report["probe_execution_status"] == "not_run"
    assert report["external_command_execution_status"] == "not_run"
    assert report["cargo_check_readiness"] == "blocked_until_pcweb_084_and_user_approval"
    assert report["normalized_probe_result"] == {
        "rustc_status": "not_run",
        "cargo_status": "not_run",
        "rustup_status": "not_run",
        "macos_xcode_select_status": "not_run",
        "macos_xcode_select_path_status": "not_run",
        "first_cargo_check_readiness": "blocked_until_pcweb_084_and_user_approval",
    }
    assert report["toolchain_presence_summary_status"] == "no_result_provided"
    for field in SAFETY_FLAGS:
        assert report[field] is False


def test_valid_caller_provided_result_is_normalized_but_cargo_check_stays_blocked():
    tool = load_tool_module()

    report = tool.build_rust_post_install_probe_result_intake_report(
        policy_path=POLICY_PATH,
        probe_result=VALID_PROBE_RESULT,
    )

    assert report["result_validation_status"] == "passed"
    assert report["result_validation_errors"] == []
    assert report["normalized_probe_result"] == VALID_PROBE_RESULT
    assert report["toolchain_presence_summary_status"] == "toolchain_probe_result_available"
    assert report["next_required_decision"] == "explicit_first_cargo_check_approval_still_required"
    assert report["cargo_check_readiness"] == "blocked_until_pcweb_084_and_user_approval"
    assert report["safe_to_run_cargo_check_now"] is False
    assert report["safe_to_run_post_install_probe_now"] is False


def test_result_intake_rejects_raw_output_paths_unknown_fields_and_ready_cargo_check():
    tool = load_tool_module()
    unsafe_probe_result = {
        **VALID_PROBE_RESULT,
        "first_cargo_check_readiness": "ready",
        "stdout": "rustc 1.90.0",
        "xcode_path": "/Library/Developer/CommandLineTools",
        "unexpected": "value",
    }

    report = tool.build_rust_post_install_probe_result_intake_report(
        policy_path=POLICY_PATH,
        probe_result=unsafe_probe_result,
    )

    assert report["result_validation_status"] == "failed"
    assert set(report["result_validation_errors"]) >= {
        "forbidden raw result field present: stdout",
        "forbidden raw result field present: xcode_path",
        "unknown result field: unexpected",
        "first_cargo_check_readiness has invalid status",
    }
    assert report["normalized_probe_result"]["first_cargo_check_readiness"] == (
        "blocked_until_pcweb_084_and_user_approval"
    )
    assert report["normalized_probe_result"]["rustc_status"] == "not_run"
    assert report["normalized_probe_result"]["cargo_status"] == "not_run"
    assert report["normalized_probe_result"]["rustup_status"] == "not_run"
    assert report["safe_to_run_cargo_check_now"] is False
    assert "/Library/Developer" not in json.dumps(report)
    assert "rustc 1.90.0" not in json.dumps(report)


def test_result_intake_does_not_echo_invalid_allowed_field_values():
    tool = load_tool_module()
    unsafe_probe_result = {
        **VALID_PROBE_RESULT,
        "rustc_status": "rustc 1.90.0 /Library/Developer Bearer SECRET_TOKEN_1234567890",
    }

    report = tool.build_rust_post_install_probe_result_intake_report(
        policy_path=POLICY_PATH,
        probe_result=unsafe_probe_result,
    )

    assert report["result_validation_status"] == "failed"
    assert "rustc_status has invalid status" in report["result_validation_errors"]
    report_json = json.dumps(report)
    assert "rustc 1.90.0" not in report_json
    assert "/Library/Developer" not in report_json
    assert "Bearer SECRET_TOKEN_1234567890" not in report_json
    assert report["normalized_probe_result"] == {
        "rustc_status": "not_run",
        "cargo_status": "not_run",
        "rustup_status": "not_run",
        "macos_xcode_select_status": "not_run",
        "macos_xcode_select_path_status": "not_run",
        "first_cargo_check_readiness": "blocked_until_pcweb_084_and_user_approval",
    }


def test_result_intake_rejects_invalid_xcode_path_status_combination():
    tool = load_tool_module()
    invalid_probe_result = {
        **VALID_PROBE_RESULT,
        "macos_xcode_select_status": "not_applicable",
        "macos_xcode_select_path_status": "path_present",
    }

    report = tool.build_rust_post_install_probe_result_intake_report(
        policy_path=POLICY_PATH,
        probe_result=invalid_probe_result,
    )

    assert report["result_validation_status"] == "failed"
    assert (
        "macos_xcode_select_path_status must be not_applicable when "
        "macos_xcode_select_status is not_applicable"
    ) in report["result_validation_errors"]


def test_result_intake_can_read_safe_caller_json_file_without_returning_path(tmp_path):
    tool = load_tool_module()
    result_path = tmp_path / "probe-result.json"
    result_path.write_text(json.dumps(VALID_PROBE_RESULT), encoding="utf-8")

    report = tool.build_rust_post_install_probe_result_intake_report(
        policy_path=POLICY_PATH,
        result_path=result_path,
    )

    assert report["result_read_status"] == "read"
    assert report["result_validation_status"] == "passed"
    assert report["normalized_probe_result"] == VALID_PROBE_RESULT
    assert str(result_path) not in json.dumps(report)


def test_custom_policy_cannot_relax_schema_flags_or_top_level_identity(tmp_path):
    tool = load_tool_module()
    custom_policy = load_policy()
    custom_policy["pcweb_id"] = "PCWEB-999"
    custom_policy["policy_name"] = "Untrusted Probe Result Intake"
    custom_policy["policy_status"] = "custom_policy_trusted"
    custom_policy["allowed_result_fields"] = ALLOWED_RESULT_FIELDS + ["stdout"]
    custom_policy["allowed_status_values"]["rustc_status"] = ["available", "ready"]
    custom_policy["forbidden_raw_result_fields"] = []
    custom_policy["next_required_decisions"] = []
    custom_policy["cargo_check_blockers"] = ["explicit_user_approval_for_first_cargo_check"]
    for field in SAFETY_FLAGS:
        custom_policy[field] = True
    custom_policy_path = tmp_path / "rust-post-install-probe-result-intake.policy.json"
    custom_policy_path.write_text(json.dumps(custom_policy), encoding="utf-8")

    report = tool.build_rust_post_install_probe_result_intake_report(
        policy_path=custom_policy_path,
        probe_result=VALID_PROBE_RESULT,
    )

    assert report["policy_validation_status"] == "failed"
    assert report["pcweb_id"] == "PCWEB-089"
    assert report["policy_name"] == "Desktop Rust Post-Install Probe Result Intake Policy"
    assert report["policy_status"] == "rust_post_install_probe_result_intake_policy_only"
    assert set(report["policy_validation_errors"]) >= {
        f"{field} must be false" for field in SAFETY_FLAGS
    }
    assert "pcweb_id must be PCWEB-089" in report["policy_validation_errors"]
    assert (
        "policy_name must be Desktop Rust Post-Install Probe Result Intake Policy"
        in report["policy_validation_errors"]
    )
    assert (
        "policy_status must be rust_post_install_probe_result_intake_policy_only"
        in report["policy_validation_errors"]
    )
    assert "allowed_result_fields must match PCWEB-089 result fields" in report[
        "policy_validation_errors"
    ]
    assert "allowed_status_values must match PCWEB-089 status enums" in report[
        "policy_validation_errors"
    ]
    assert "forbidden_raw_result_fields must match PCWEB-089 forbidden raw fields" in report[
        "policy_validation_errors"
    ]
    assert "next_required_decisions must match PCWEB-089 required decisions" in report[
        "policy_validation_errors"
    ]
    assert "cargo_check_blockers must match PCWEB-089 cargo check blockers" in report[
        "policy_validation_errors"
    ]
    assert report["allowed_result_fields"] == ALLOWED_RESULT_FIELDS
    assert report["allowed_status_values"] == ALLOWED_STATUS_VALUES
    assert report["forbidden_raw_result_fields"] == FORBIDDEN_RAW_RESULT_FIELDS
    assert report["result_validation_status"] == "blocked_by_policy_validation"
    for field in SAFETY_FLAGS:
        assert report[field] is False


def test_policy_and_result_paths_reject_forbidden_roots_before_reading(tmp_path, monkeypatch):
    tool = load_tool_module()
    repo_root = tmp_path / "repo"
    external_root = tmp_path / "outside"
    monkeypatch.setattr(tool, "REPO_ROOT", repo_root)

    mixed_case_roots = {
        ("CONFIGS", "LOCAL"): "configs/local",
        ("Data", "Local_Runtime"): "data/local_runtime",
        ("OUTPUTS",): "outputs",
        ("Artifacts", "Tmp"): "artifacts/tmp",
        ("Data", "ASR_Eval", "Samples"): "data/asr_eval/samples",
    }

    for suffix_parts, label in mixed_case_roots.items():
        for forbidden_path in [
            repo_root.joinpath(*suffix_parts) / "rust-post-install-probe-result-intake.policy.json",
            external_root.joinpath(*suffix_parts) / "probe-result.json",
        ]:
            report = tool.build_rust_post_install_probe_result_intake_report(
                policy_path=forbidden_path if forbidden_path.name.endswith(".json") else POLICY_PATH,
                result_path=forbidden_path,
            )

            assert report["policy_validation_status"] == "failed"
            assert report["policy_validation_errors"] == [
                f"path is blocked: {label}",
            ]
            assert report["result_read_status"] == "blocked"
            assert report["result_validation_status"] == "blocked_by_policy_validation"
            for field in SAFETY_FLAGS:
                assert report[field] is False


def test_probe_result_intake_tool_source_has_no_command_execution_entrypoints():
    source = TOOL_PATH.read_text(encoding="utf-8")

    forbidden_source_tokens = [
        "subprocess",
        "os.system",
        "Popen",
        "check_call",
        "check_output",
        "run(",
        "exec(",
        "eval(",
    ]
    for token in forbidden_source_tokens:
        assert token not in source
