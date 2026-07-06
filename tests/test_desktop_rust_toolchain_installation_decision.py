import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = REPO_ROOT / "code" / "desktop_tauri" / "rust-toolchain-installation.policy.json"
TOOL_PATH = REPO_ROOT / "tools" / "desktop_rust_toolchain_installation_decision.py"

SAFETY_FLAGS = [
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
]


def load_tool_module():
    spec = importlib.util.spec_from_file_location("desktop_rust_toolchain_installation_decision", TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_policy() -> dict:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def test_rust_toolchain_installation_policy_exists_and_blocks_side_effects():
    policy = load_policy()

    assert policy["pcweb_id"] == "PCWEB-086"
    assert policy["policy_status"] == "rust_toolchain_installation_decision_policy_only"
    assert policy["default_quality_gate_status"] == "included_in_root_pytest"
    assert policy["installation_decision_mode"] == "no_install_decision_report_only"
    assert policy["recommended_install_provider"] == "official_rustup"
    for field in SAFETY_FLAGS:
        assert policy[field] is False

    assert policy["required_approval_tokens_before_install"] == REQUIRED_APPROVAL_TOKENS
    assert "rustc_version" in policy["post_install_verification_order"]
    assert "cargo_version" in policy["post_install_verification_order"]
    assert "rustup_version" in policy["post_install_verification_order"]
    assert "macos_xcode_select_presence_redacted" in policy["post_install_verification_order"]
    assert "pcweb_084_cargo_check_preflight" in policy["post_install_verification_order"]


def test_policy_records_official_sources_platform_notes_and_forbidden_commands():
    policy = load_policy()

    source_urls = {source["url"] for source in policy["official_sources"]}
    assert "https://www.rust-lang.org/tools/install" in source_urls
    assert "https://rust-lang.github.io/rustup/" in source_urls
    assert "https://doc.rust-lang.org/cargo/commands/cargo-check.html" in source_urls
    assert "https://doc.rust-lang.org/cargo/reference/build-cache.html" in source_urls
    assert "https://v2.tauri.app/start/prerequisites/" in source_urls

    assert set(policy["platform_notes"]) == {"macos", "windows", "linux"}
    assert "xcode_command_line_tools_available" in policy["platform_notes"]["macos"]["current_pcweb_085_probe_summary"]
    assert "visual_studio_build_tools_required_for_windows_future_build" in policy["platform_notes"]["windows"]["future_prerequisites"]
    assert "system_package_manager_approval_required" in policy["platform_notes"]["linux"]["future_prerequisites"]

    forbidden_commands = {" ".join(command) for command in policy["forbidden_commands"]}
    assert {
        "curl https://sh.rustup.rs -sSf",
        "sh rustup-init",
        "rustup toolchain install",
        "rustup update",
        "cargo check",
        "cargo build",
        "brew install rustup",
        "xcode-select --install",
        "winget install",
        "apt install",
        "npm install",
        "npx tauri build",
    }.issubset(forbidden_commands)
    assert {
        "shell_profile_modification",
        "installer_execution",
        "dependency_download",
        "cargo_artifact_generation",
        "configs_local_read",
        "secret_read",
        "remote_asr_call",
        "remote_llm_call",
    }.issubset(policy["forbidden_default_side_effects"])


def test_installation_decision_report_is_static_and_never_executable():
    tool = load_tool_module()

    report = tool.build_rust_toolchain_installation_decision_report(policy_path=POLICY_PATH)

    assert report["pcweb_id"] == "PCWEB-086"
    assert report["report_mode"] == "rust_toolchain_installation_decision_static_report"
    assert report["policy_validation_status"] == "passed"
    assert report["installation_execution_status"] == "not_run"
    assert report["external_command_execution_status"] == "not_run"
    assert report["installation_decision_status"] == "blocked_requires_explicit_user_approval"
    assert report["recommended_install_provider"] == "official_rustup"
    assert report["approval_blockers"] == REQUIRED_APPROVAL_TOKENS
    assert report["post_install_verification_order"] == [
        "rustc_version",
        "cargo_version",
        "rustup_version",
        "macos_xcode_select_presence_redacted",
        "pcweb_084_cargo_check_preflight",
    ]
    for field in SAFETY_FLAGS:
        assert report[field] is False


def test_custom_policy_cannot_relax_safety_flags_or_remove_approval_tokens(tmp_path):
    tool = load_tool_module()
    custom_policy = load_policy()
    for field in SAFETY_FLAGS:
        custom_policy[field] = True
    custom_policy["required_approval_tokens_before_install"] = REQUIRED_APPROVAL_TOKENS[:-1]
    custom_policy_path = tmp_path / "rust-toolchain-installation.policy.json"
    custom_policy_path.write_text(json.dumps(custom_policy), encoding="utf-8")

    report = tool.build_rust_toolchain_installation_decision_report(policy_path=custom_policy_path)

    assert report["policy_validation_status"] == "failed"
    assert set(report["policy_validation_errors"]) >= {
        f"{field} must be false" for field in SAFETY_FLAGS
    }
    assert "required_approval_tokens_before_install must match PCWEB-086 required tokens" in report[
        "policy_validation_errors"
    ]
    assert report["installation_execution_status"] == "not_run"
    assert report["installation_decision_status"] == "blocked_by_policy_validation"
    for field in SAFETY_FLAGS:
        assert report[field] is False


def test_custom_policy_path_rejects_configs_local_before_reading(tmp_path, monkeypatch):
    tool = load_tool_module()
    monkeypatch.setattr(tool, "REPO_ROOT", tmp_path)
    forbidden_policy_path = tmp_path / "configs" / "local" / "rust-toolchain-installation.policy.json"

    report = tool.build_rust_toolchain_installation_decision_report(policy_path=forbidden_policy_path)

    assert report["pcweb_id"] == "PCWEB-086"
    assert report["policy_validation_status"] == "failed"
    assert report["policy_validation_errors"] == [
        "policy path is blocked: configs/local",
    ]
    assert report["installation_execution_status"] == "not_run"
    assert report["installation_decision_status"] == "blocked_by_policy_validation"
    assert report["policy_read_status"] == "blocked"
    for field in SAFETY_FLAGS:
        assert report[field] is False


def test_installation_decision_tool_source_has_no_command_execution_entrypoints():
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
