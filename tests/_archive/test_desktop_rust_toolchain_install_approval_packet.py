import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = REPO_ROOT / "code" / "desktop_tauri" / "rust-toolchain-install-approval.policy.json"
TOOL_PATH = REPO_ROOT / "tools" / "desktop_rust_toolchain_install_approval_packet.py"

SAFETY_FLAGS = [
    "safe_to_execute_install_now",
    "safe_to_install_toolchain_now",
    "safe_to_modify_shell_profile_now",
    "safe_to_run_install_command_now",
    "safe_to_run_rustup_now",
    "safe_to_run_cargo_check_now",
    "safe_to_run_cargo_build_now",
    "safe_to_run_tauri_dev_now",
    "safe_to_run_tauri_build_now",
    "safe_to_fetch_dependencies_now",
    "safe_to_generate_cargo_lock_now",
    "safe_to_generate_target_dir_now",
    "safe_to_read_configs_local_now",
]

REQUIRED_APPROVAL_TOKENS = [
    "explicit_user_approval_for_rust_toolchain_install",
    "approved_install_provider_official_rustup",
    "approved_shell_profile_modification_policy",
    "approved_network_download_policy_for_rustup",
    "approved_post_install_probe_policy",
    "no_audio_worker_secret_remote_boundary_reconfirmed",
    "approved_manual_user_run_only_boundary",
    "approved_rustup_uninstall_or_rollback_understanding",
]

POST_INSTALL_VERIFICATION_ORDER = [
    "rustc_version",
    "cargo_version",
    "rustup_version",
    "macos_xcode_select_presence_redacted",
    "pcweb_084_cargo_check_preflight",
]

OFFICIAL_SOURCE_URLS = {
    "https://www.rust-lang.org/tools/install",
    "https://rust-lang.github.io/rustup/installation/index.html",
    "https://v2.tauri.app/start/prerequisites/",
}


def load_tool_module():
    spec = importlib.util.spec_from_file_location("desktop_rust_toolchain_install_approval_packet", TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_policy() -> dict:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def test_install_approval_policy_exists_and_records_manual_boundary():
    policy = load_policy()

    assert policy["pcweb_id"] == "PCWEB-087"
    assert policy["policy_status"] == "rust_toolchain_install_approval_packet_policy_only"
    assert policy["default_quality_gate_status"] == "included_in_root_pytest"
    assert policy["approval_packet_mode"] == "manual_user_run_only"
    assert policy["recommended_install_provider"] == "official_rustup"
    assert policy["manual_instruction_text_status"] == "inert_text_only"
    assert policy["command_execution_status"] == "not_run"
    assert policy["installation_execution_status"] == "not_run"
    for field in SAFETY_FLAGS:
        assert policy[field] is False

    assert policy["required_approval_tokens_before_install"] == REQUIRED_APPROVAL_TOKENS
    assert policy["post_install_verification_order"] == POST_INSTALL_VERIFICATION_ORDER


def test_policy_records_official_sources_manual_text_platform_risks_and_rollback():
    policy = load_policy()

    source_urls = {source["url"] for source in policy["official_sources"]}
    assert OFFICIAL_SOURCE_URLS.issubset(source_urls)

    manual_text = policy["manual_instruction_text_by_platform"]
    assert set(manual_text) == {"macos", "windows", "linux"}
    assert (
        manual_text["macos"]["manual_command_text"]
        == "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh"
    )
    assert "manual_user_run_only" in manual_text["macos"]["execution_boundary"]
    assert "rustup-init.exe" in manual_text["windows"]["manual_installer_text"]
    assert "manual_text_only_not_executable_by_tool" in manual_text["windows"]["execution_boundary"]
    assert (
        manual_text["linux"]["manual_command_text"]
        == "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh"
    )

    assert set(policy["platform_notes"]) == {"macos", "windows", "linux"}
    assert "path_or_shell_profile_may_change" in policy["risk_notes"]
    assert "network_download_required_by_rustup" in policy["risk_notes"]
    assert "manual_rustup_self_uninstall_only" in policy["rollback_notes"]
    assert "manual_path_recovery_review_required" in policy["rollback_notes"]


def test_install_approval_report_is_static_and_never_executes_manual_text():
    tool = load_tool_module()

    report = tool.build_rust_toolchain_install_approval_packet(policy_path=POLICY_PATH)

    assert report["pcweb_id"] == "PCWEB-087"
    assert report["report_mode"] == "rust_toolchain_install_approval_packet_static_report"
    assert report["policy_validation_status"] == "passed"
    assert report["approval_packet_status"] == "generated_for_manual_review"
    assert report["execution_mode"] == "manual_user_run_only"
    assert report["manual_instruction_text_status"] == "inert_text_only"
    assert report["command_execution_status"] == "not_run"
    assert report["external_command_execution_status"] == "not_run"
    assert report["installation_execution_status"] == "not_run"
    assert report["safe_to_execute_install_now"] is False
    assert report["approval_blockers"] == REQUIRED_APPROVAL_TOKENS
    assert report["post_install_verification_order"] == POST_INSTALL_VERIFICATION_ORDER
    assert (
        report["manual_instruction_text_by_platform"]["macos"]["manual_command_text"]
        == "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh"
    )
    for field in SAFETY_FLAGS:
        assert report[field] is False


def test_custom_policy_cannot_relax_safety_flags_remove_tokens_or_enable_execution(tmp_path):
    tool = load_tool_module()
    custom_policy = load_policy()
    for field in SAFETY_FLAGS:
        custom_policy[field] = True
    custom_policy["approval_packet_mode"] = "execute_install"
    custom_policy["command_execution_status"] = "ready"
    custom_policy["installation_execution_status"] = "ready"
    custom_policy["manual_instruction_text_status"] = "executable"
    custom_policy["required_approval_tokens_before_install"] = REQUIRED_APPROVAL_TOKENS[:-1]
    custom_policy["official_sources"] = []
    custom_policy["manual_instruction_text_by_platform"]["macos"][
        "execution_boundary"
    ] = "execute_by_tool"
    custom_policy_path = tmp_path / "rust-toolchain-install-approval.policy.json"
    custom_policy_path.write_text(json.dumps(custom_policy), encoding="utf-8")

    report = tool.build_rust_toolchain_install_approval_packet(policy_path=custom_policy_path)

    assert report["policy_validation_status"] == "failed"
    assert set(report["policy_validation_errors"]) >= {
        f"{field} must be false" for field in SAFETY_FLAGS
    }
    assert "approval_packet_mode must be manual_user_run_only" in report["policy_validation_errors"]
    assert "command_execution_status must be not_run" in report["policy_validation_errors"]
    assert "installation_execution_status must be not_run" in report["policy_validation_errors"]
    assert "manual_instruction_text_status must be inert_text_only" in report["policy_validation_errors"]
    assert "required_approval_tokens_before_install must match PCWEB-087 required tokens" in report[
        "policy_validation_errors"
    ]
    assert "official_sources must contain required PCWEB-087 official URLs" in report[
        "policy_validation_errors"
    ]
    assert report["approval_packet_status"] == "blocked_by_policy_validation"
    assert report["command_execution_status"] == "not_run"
    assert report["installation_execution_status"] == "not_run"
    assert {source["url"] for source in report["official_sources"]} == OFFICIAL_SOURCE_URLS
    assert (
        report["manual_instruction_text_by_platform"]["macos"]["execution_boundary"]
        == "manual_user_run_only"
    )
    assert (
        report["manual_instruction_text_by_platform"]["windows"]["execution_boundary"]
        == "manual_text_only_not_executable_by_tool"
    )
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
        forbidden_policy_path = tmp_path.joinpath(*suffix_parts) / "rust-toolchain-install-approval.policy.json"

        report = tool.build_rust_toolchain_install_approval_packet(policy_path=forbidden_policy_path)

        assert report["pcweb_id"] == "PCWEB-087"
        assert report["policy_validation_status"] == "failed"
        assert report["policy_validation_errors"] == [
            f"policy path is blocked: {label}",
        ]
        assert report["policy_read_status"] == "blocked"
        assert report["approval_packet_status"] == "blocked_by_policy_validation"
        assert report["command_execution_status"] == "not_run"
        assert report["installation_execution_status"] == "not_run"
        for field in SAFETY_FLAGS:
            assert report[field] is False


def test_custom_policy_path_rejects_forbidden_roots_outside_repo_before_reading(tmp_path, monkeypatch):
    tool = load_tool_module()
    repo_root = tmp_path / "repo"
    external_root = tmp_path / "outside"
    monkeypatch.setattr(tool, "REPO_ROOT", repo_root)

    forbidden_policy_path = (
        external_root / "configs" / "local" / "rust-toolchain-install-approval.policy.json"
    )

    report = tool.build_rust_toolchain_install_approval_packet(policy_path=forbidden_policy_path)

    assert report["policy_validation_status"] == "failed"
    assert report["policy_validation_errors"] == [
        "policy path is blocked: configs/local",
    ]
    assert report["policy_read_status"] == "blocked"
    assert report["approval_packet_status"] == "blocked_by_policy_validation"


def test_install_approval_tool_source_has_no_command_execution_entrypoints():
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
