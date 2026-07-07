import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = REPO_ROOT / "code" / "desktop_tauri" / "rust-post-install-probe-approval.policy.json"
TOOL_PATH = REPO_ROOT / "tools" / "desktop_rust_post_install_probe_approval.py"

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
    "safe_to_read_shell_profiles_now",
    "safe_to_read_cargo_home_now",
    "safe_to_read_rustup_home_now",
]

REQUIRED_APPROVAL_TOKENS = [
    "explicit_user_approval_for_post_install_probe",
    "rust_toolchain_install_completed_by_user",
    "approved_post_install_probe_command_allowlist",
    "approved_probe_output_redaction_policy",
    "approved_no_cargo_check_boundary_reconfirmed",
    "no_audio_worker_secret_remote_boundary_reconfirmed",
]

EXPECTED_ALLOWLIST = [
    {
        "probe_id": "rustc_version",
        "command_text": "rustc --version",
        "platform": "all",
        "output_policy": "version_text_only",
    },
    {
        "probe_id": "cargo_version",
        "command_text": "cargo --version",
        "platform": "all",
        "output_policy": "version_text_only",
    },
    {
        "probe_id": "rustup_version",
        "command_text": "rustup --version",
        "platform": "all",
        "output_policy": "version_text_only",
    },
    {
        "probe_id": "macos_xcode_select_presence",
        "command_text": "xcode-select -p",
        "platform": "macos",
        "output_policy": "presence_only_no_path",
    },
]

EXPECTED_RESULT_SCHEMA_FIELDS = [
    "rustc_status",
    "cargo_status",
    "rustup_status",
    "macos_xcode_select_status",
    "macos_xcode_select_path_status",
    "first_cargo_check_readiness",
]

OFFICIAL_SOURCE_URLS = {
    "https://www.rust-lang.org/tools/install",
    "https://rust-lang.github.io/rustup/installation/index.html",
    "https://doc.rust-lang.org/cargo/commands/cargo-check.html",
    "https://doc.rust-lang.org/cargo/reference/build-cache.html",
    "https://v2.tauri.app/start/prerequisites/",
}

FORBIDDEN_DEFAULT_SIDE_EFFECTS = [
    "probe_command_execution",
    "shell_execution",
    "installer_execution",
    "package_manager_execution",
    "cargo_command_execution",
    "tauri_command_execution",
    "shell_profile_read",
    "path_environment_read",
    "cargo_home_read",
    "rustup_home_read",
    "dependency_cache_read",
    "cargo_lock_read",
    "target_dir_read",
    "dependency_download",
    "cargo_artifact_generation",
    "audio_permission_request",
    "audio_device_enumeration",
    "microphone_capture",
    "system_audio_capture",
    "asr_worker_spawn",
    "provider_config_read",
    "secret_read",
    "configs_local_read",
    "remote_asr_call",
    "remote_llm_call",
    "installer_creation",
    "signing",
    "notarization",
]


def load_tool_module():
    spec = importlib.util.spec_from_file_location("desktop_rust_post_install_probe_approval", TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_policy() -> dict:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def test_post_install_probe_approval_policy_exists_and_blocks_execution():
    policy = load_policy()

    assert policy["pcweb_id"] == "PCWEB-088"
    assert policy["policy_status"] == "rust_post_install_probe_approval_policy_only"
    assert policy["default_quality_gate_status"] == "included_in_root_pytest"
    assert policy["probe_approval_mode"] == "no_probe_execution_approval_packet_only"
    assert policy["probe_execution_status"] == "not_run"
    assert policy["external_command_execution_status"] == "not_run"
    assert policy["cargo_check_readiness"] == "blocked_until_pcweb_084_and_user_approval"
    for field in SAFETY_FLAGS:
        assert policy[field] is False

    assert policy["required_approval_tokens_before_probe"] == REQUIRED_APPROVAL_TOKENS


def test_policy_records_exact_future_probe_allowlist_schema_and_redaction():
    policy = load_policy()

    assert policy["future_probe_command_allowlist"] == EXPECTED_ALLOWLIST
    assert policy["expected_probe_result_schema_fields"] == EXPECTED_RESULT_SCHEMA_FIELDS
    assert policy["redaction_requirements"] == [
        "macos_xcode_select_path_presence_only",
        "do_not_return_shell_profile_paths",
        "do_not_return_path_environment",
        "do_not_return_cargo_home",
        "do_not_return_rustup_home",
        "do_not_return_dependency_cache_paths",
        "do_not_return_credentials_or_provider_config",
    ]
    source_urls = {source["url"] for source in policy["official_sources"]}
    assert OFFICIAL_SOURCE_URLS.issubset(source_urls)
    assert "explicit_user_approval_for_first_cargo_check" in policy["cargo_check_blockers"]
    assert "pcweb_084_artifact_policy_reacknowledged" in policy["cargo_check_blockers"]


def test_post_install_probe_approval_report_is_static_and_never_runs_probes():
    tool = load_tool_module()

    report = tool.build_rust_post_install_probe_approval_report(policy_path=POLICY_PATH)

    assert report["pcweb_id"] == "PCWEB-088"
    assert report["report_mode"] == "rust_post_install_probe_approval_static_report"
    assert report["policy_validation_status"] == "passed"
    assert report["probe_approval_status"] == "generated_for_manual_review"
    assert report["probe_approval_mode"] == "no_probe_execution_approval_packet_only"
    assert report["probe_execution_status"] == "not_run"
    assert report["external_command_execution_status"] == "not_run"
    assert report["cargo_check_readiness"] == "blocked_until_pcweb_084_and_user_approval"
    assert report["future_probe_command_allowlist"] == EXPECTED_ALLOWLIST
    assert report["approval_blockers"] == REQUIRED_APPROVAL_TOKENS
    assert report["expected_probe_result_schema_fields"] == EXPECTED_RESULT_SCHEMA_FIELDS
    for field in SAFETY_FLAGS:
        assert report[field] is False


def test_custom_policy_cannot_add_probe_commands_relax_flags_or_remove_redaction(tmp_path):
    tool = load_tool_module()
    custom_policy = load_policy()
    custom_policy["pcweb_id"] = "PCWEB-999"
    custom_policy["policy_name"] = "Untrusted Custom Probe Policy"
    custom_policy["policy_status"] = "custom_policy_trusted"
    custom_policy["future_probe_command_allowlist"].append(
        {
            "probe_id": "cargo_check",
            "command_text": "cargo check",
            "platform": "all",
            "output_policy": "raw",
        }
    )
    for field in SAFETY_FLAGS:
        custom_policy[field] = True
    custom_policy["probe_approval_mode"] = "execute_probe"
    custom_policy["probe_execution_status"] = "ready"
    custom_policy["external_command_execution_status"] = "ready"
    custom_policy["cargo_check_readiness"] = "ready"
    custom_policy["required_approval_tokens_before_probe"] = REQUIRED_APPROVAL_TOKENS[:-1]
    custom_policy["redaction_requirements"] = []
    custom_policy["official_sources"] = []
    custom_policy["forbidden_default_side_effects"] = []
    custom_policy_path = tmp_path / "rust-post-install-probe-approval.policy.json"
    custom_policy_path.write_text(json.dumps(custom_policy), encoding="utf-8")

    report = tool.build_rust_post_install_probe_approval_report(policy_path=custom_policy_path)

    assert report["policy_validation_status"] == "failed"
    assert report["pcweb_id"] == "PCWEB-088"
    assert report["policy_name"] == "Desktop Rust Post-Install Probe Approval Policy"
    assert report["policy_status"] == "rust_post_install_probe_approval_policy_only"
    assert set(report["policy_validation_errors"]) >= {
        f"{field} must be false" for field in SAFETY_FLAGS
    }
    assert "pcweb_id must be PCWEB-088" in report["policy_validation_errors"]
    assert (
        "policy_name must be Desktop Rust Post-Install Probe Approval Policy"
        in report["policy_validation_errors"]
    )
    assert (
        "policy_status must be rust_post_install_probe_approval_policy_only"
        in report["policy_validation_errors"]
    )
    assert "future_probe_command_allowlist must match PCWEB-088 allowlist" in report[
        "policy_validation_errors"
    ]
    assert "probe_approval_mode must be no_probe_execution_approval_packet_only" in report[
        "policy_validation_errors"
    ]
    assert "probe_execution_status must be not_run" in report["policy_validation_errors"]
    assert "external_command_execution_status must be not_run" in report["policy_validation_errors"]
    assert "cargo_check_readiness must be blocked_until_pcweb_084_and_user_approval" in report[
        "policy_validation_errors"
    ]
    assert "required_approval_tokens_before_probe must match PCWEB-088 required tokens" in report[
        "policy_validation_errors"
    ]
    assert "redaction_requirements must match PCWEB-088 redaction requirements" in report[
        "policy_validation_errors"
    ]
    assert "official_sources must contain required PCWEB-088 official URLs" in report[
        "policy_validation_errors"
    ]
    assert "forbidden_default_side_effects must match PCWEB-088 forbidden side effects" in report[
        "policy_validation_errors"
    ]
    assert report["probe_approval_status"] == "blocked_by_policy_validation"
    assert report["future_probe_command_allowlist"] == EXPECTED_ALLOWLIST
    assert {source["url"] for source in report["official_sources"]} == OFFICIAL_SOURCE_URLS
    assert report["forbidden_default_side_effects"] == FORBIDDEN_DEFAULT_SIDE_EFFECTS
    for field in SAFETY_FLAGS:
        assert report[field] is False


def test_custom_policy_path_rejects_forbidden_roots_before_reading(tmp_path, monkeypatch):
    tool = load_tool_module()
    monkeypatch.setattr(tool, "REPO_ROOT", tmp_path)

    forbidden_roots = {
        ("configs", "local"): "configs/local",
        ("data", "local_runtime"): "data/local_runtime",
        ("outputs",): "outputs",
        ("artifacts", "tmp"): "artifacts/tmp",
        ("data", "asr_eval", "samples"): "data/asr_eval/samples",
    }

    for suffix_parts, label in forbidden_roots.items():
        forbidden_policy_path = tmp_path.joinpath(*suffix_parts) / "rust-post-install-probe-approval.policy.json"

        report = tool.build_rust_post_install_probe_approval_report(policy_path=forbidden_policy_path)

        assert report["pcweb_id"] == "PCWEB-088"
        assert report["policy_validation_status"] == "failed"
        assert report["policy_validation_errors"] == [
            f"policy path is blocked: {label}",
        ]
        assert report["policy_read_status"] == "blocked"
        assert report["probe_approval_status"] == "blocked_by_policy_validation"
        assert report["probe_execution_status"] == "not_run"
        for field in SAFETY_FLAGS:
            assert report[field] is False


def test_custom_policy_path_rejects_forbidden_roots_outside_repo_before_reading(tmp_path, monkeypatch):
    tool = load_tool_module()
    repo_root = tmp_path / "repo"
    external_root = tmp_path / "outside"
    monkeypatch.setattr(tool, "REPO_ROOT", repo_root)

    forbidden_policy_path = (
        external_root / "configs" / "local" / "rust-post-install-probe-approval.policy.json"
    )

    report = tool.build_rust_post_install_probe_approval_report(policy_path=forbidden_policy_path)

    assert report["policy_validation_status"] == "failed"
    assert report["policy_validation_errors"] == [
        "policy path is blocked: configs/local",
    ]
    assert report["policy_read_status"] == "blocked"
    assert report["probe_approval_status"] == "blocked_by_policy_validation"


def test_custom_policy_path_rejects_mixed_case_forbidden_roots_before_reading(tmp_path, monkeypatch):
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
        in_repo_policy_path = repo_root.joinpath(*suffix_parts) / "rust-post-install-probe-approval.policy.json"
        outside_policy_path = (
            external_root.joinpath(*suffix_parts) / "rust-post-install-probe-approval.policy.json"
        )

        for forbidden_policy_path in [in_repo_policy_path, outside_policy_path]:
            report = tool.build_rust_post_install_probe_approval_report(policy_path=forbidden_policy_path)

            assert report["policy_validation_status"] == "failed"
            assert report["policy_validation_errors"] == [
                f"policy path is blocked: {label}",
            ]
            assert report["policy_read_status"] == "blocked"
            assert report["probe_approval_status"] == "blocked_by_policy_validation"


def test_post_install_probe_tool_source_has_no_command_execution_entrypoints():
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
