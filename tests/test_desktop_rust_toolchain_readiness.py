import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = REPO_ROOT / "code" / "desktop_tauri" / "rust-toolchain-readiness.policy.json"
TOOL_PATH = REPO_ROOT / "tools" / "desktop_rust_toolchain_readiness.py"


def load_tool_module():
    spec = importlib.util.spec_from_file_location("desktop_rust_toolchain_readiness", TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_policy() -> dict:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def test_rust_toolchain_readiness_policy_exists_and_blocks_side_effects():
    policy = load_policy()

    assert policy["pcweb_id"] == "PCWEB-085"
    assert policy["policy_status"] == "rust_toolchain_readiness_policy_only"
    assert policy["default_quality_gate_status"] == "included_in_root_pytest"
    assert policy["toolchain_probe_mode"] == "local_version_and_platform_probe_only"
    assert policy["safe_to_install_toolchain_now"] is False
    assert policy["safe_to_modify_shell_profile_now"] is False
    assert policy["safe_to_run_cargo_check_now"] is False
    assert policy["safe_to_run_cargo_build_now"] is False
    assert policy["safe_to_run_tauri_dev_now"] is False
    assert policy["safe_to_run_tauri_build_now"] is False
    assert policy["safe_to_fetch_dependencies_now"] is False
    assert policy["safe_to_generate_cargo_lock_now"] is False
    assert policy["safe_to_generate_target_dir_now"] is False
    assert policy["safe_to_read_configs_local_now"] is False


def test_policy_limits_probe_commands_and_forbids_install_build_commands():
    policy = load_policy()

    assert policy["allowed_probe_commands"] == [
        ["rustc", "--version"],
        ["cargo", "--version"],
        ["rustup", "--version"],
        ["xcode-select", "-p"],
    ]
    forbidden_commands = {" ".join(command) for command in policy["forbidden_commands"]}
    assert {
        "cargo check",
        "cargo build",
        "cargo tauri dev",
        "cargo tauri build",
        "rustup update",
        "rustup toolchain install",
        "curl https://sh.rustup.rs -sSf",
        "sh rustup-init",
        "npm install",
        "npm ci",
        "pnpm install",
        "yarn install",
        "npx tauri dev",
        "npx tauri build",
    }.issubset(forbidden_commands)
    assert policy["probe_output_redaction_policy"]["xcode_select_path"] == "presence_only"

    assert {
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
        "shell_profile_modification",
        "installer_execution",
    }.issubset(policy["forbidden_default_side_effects"])


def test_policy_records_sources_and_first_cargo_check_preconditions():
    policy = load_policy()

    assert set(policy["remaining_preconditions_before_first_cargo_check"]) == {
        "explicit_user_approval_for_first_cargo_check",
        "rustc_available",
        "cargo_available",
        "macos_command_line_tools_available_or_non_macos_equivalent",
        "pcweb_084_artifact_policy_acknowledged",
        "first_dependency_resolution_network_fetch_approved_or_cache_preseeded",
        "no_audio_worker_secret_remote_boundary_reconfirmed",
    }
    source_urls = {source["url"] for source in policy["official_sources"]}
    assert "https://www.rust-lang.org/tools/install" in source_urls
    assert "https://rust-lang.github.io/rustup/concepts/components.html" in source_urls
    assert "https://doc.rust-lang.org/cargo/commands/cargo-check.html" in source_urls
    assert "https://doc.rust-lang.org/cargo/reference/build-cache.html" in source_urls
    assert "https://v2.tauri.app/start/prerequisites/" in source_urls


def test_toolchain_readiness_report_is_static_by_default_and_does_not_probe():
    tool = load_tool_module()
    calls = []

    report = tool.build_rust_toolchain_readiness_report(
        policy_path=POLICY_PATH,
        probe_local_toolchain=False,
        runner=calls.append,
    )

    assert calls == []
    assert report["pcweb_id"] == "PCWEB-085"
    assert report["report_mode"] == "rust_toolchain_readiness_static_report"
    assert report["policy_validation_status"] == "passed"
    assert report["toolchain_probe_status"] == "not_run"
    assert report["toolchain_probe_results"] == []
    assert report["first_cargo_check_readiness_status"] == "not_evaluated"
    assert report["safe_to_install_toolchain_now"] is False
    assert report["safe_to_run_cargo_check_now"] is False


def test_optional_probe_runs_only_allowlisted_commands_and_redacts_xcode_path():
    tool = load_tool_module()
    calls = []

    def fake_runner(command):
        calls.append(command)
        stdout_by_command = {
            "rustc --version": "rustc 1.90.0",
            "cargo --version": "cargo 1.90.0",
            "rustup --version": "rustup 1.28.0",
            "xcode-select -p": "/Library/Developer/CommandLineTools",
        }
        return {
            "command": command,
            "returncode": 0,
            "stdout": stdout_by_command[" ".join(command)],
            "stderr": "",
        }

    report = tool.build_rust_toolchain_readiness_report(
        policy_path=POLICY_PATH,
        probe_local_toolchain=True,
        runner=fake_runner,
    )

    assert calls == [
        ["rustc", "--version"],
        ["cargo", "--version"],
        ["rustup", "--version"],
        ["xcode-select", "-p"],
    ]
    assert report["report_mode"] == "local_version_and_platform_probe_only"
    assert report["toolchain_probe_status"] == "probed"
    assert report["toolchain_probe_summary"]["rustc_status"] == "available"
    assert report["toolchain_probe_summary"]["cargo_status"] == "available"
    assert report["toolchain_probe_summary"]["rustup_status"] == "available"
    assert report["toolchain_probe_summary"]["macos_command_line_tools_status"] == "available"
    assert report["first_cargo_check_readiness_status"] == "blocked_until_explicit_approval_for_first_cargo_check"
    xcode_result = report["toolchain_probe_results"][-1]
    assert xcode_result["command"] == ["xcode-select", "-p"]
    assert xcode_result["stdout"] == "[redacted:xcode-select-path-present]"
    assert xcode_result["path_status"] == "path_present"
    assert "/Library/Developer" not in json.dumps(report)
    assert report["safe_to_run_cargo_check_now"] is False


def test_xcode_select_probe_redacts_path_from_stderr():
    tool = load_tool_module()

    result = tool.run_allowed_probe_command(
        ["xcode-select", "-p"],
        lambda command: {
            "command": command,
            "returncode": 0,
            "stdout": "/Library/Developer/CommandLineTools",
            "stderr": "selected path: /Library/Developer/CommandLineTools",
        },
    )

    serialized = json.dumps(result)
    assert result["stdout"] == "[redacted:xcode-select-path-present]"
    assert result["stderr"] == ""
    assert result["path_status"] == "path_present"
    assert "/Library/Developer" not in serialized


def test_custom_policy_cannot_expand_toolchain_probe_allowlist(tmp_path):
    tool = load_tool_module()
    custom_policy = load_policy()
    custom_policy["allowed_probe_commands"] = [
        ["rustc", "--version"],
        ["cargo", "check"],
    ]
    custom_policy_path = tmp_path / "rust-toolchain-readiness.policy.json"
    custom_policy_path.write_text(json.dumps(custom_policy), encoding="utf-8")
    calls = []

    def fake_runner(command):
        calls.append(command)
        return {
            "command": command,
            "returncode": 0,
            "stdout": f"{command[0]} ok",
            "stderr": "",
        }

    report = tool.build_rust_toolchain_readiness_report(
        policy_path=custom_policy_path,
        probe_local_toolchain=True,
        runner=fake_runner,
    )

    assert calls == [["rustc", "--version"]]
    assert report["toolchain_probe_results"] == [
        {
            "command": ["rustc", "--version"],
            "returncode": 0,
            "stdout": "rustc ok",
            "stderr": "",
        },
        {
            "command": ["cargo", "check"],
            "returncode": 126,
            "stdout": "",
            "stderr": "blocked by desktop rust toolchain readiness probe allowlist",
        },
    ]
    assert report["safe_to_run_cargo_check_now"] is False


def test_custom_policy_cannot_relax_safety_flags(tmp_path):
    tool = load_tool_module()
    custom_policy = load_policy()
    relaxed_fields = [
        "safe_to_install_toolchain_now",
        "safe_to_modify_shell_profile_now",
        "safe_to_run_cargo_check_now",
        "safe_to_run_cargo_build_now",
        "safe_to_run_tauri_dev_now",
        "safe_to_run_tauri_build_now",
        "safe_to_fetch_dependencies_now",
        "safe_to_generate_cargo_lock_now",
        "safe_to_generate_target_dir_now",
        "safe_to_read_configs_local_now",
    ]
    for field in relaxed_fields:
        custom_policy[field] = True
    custom_policy_path = tmp_path / "relaxed-rust-toolchain-readiness.policy.json"
    custom_policy_path.write_text(json.dumps(custom_policy), encoding="utf-8")

    report = tool.build_rust_toolchain_readiness_report(
        policy_path=custom_policy_path,
        probe_local_toolchain=False,
    )

    assert report["policy_validation_status"] == "failed"
    assert set(report["policy_validation_errors"]) >= {
        f"{field} must be false" for field in relaxed_fields
    }
    for field in relaxed_fields:
        assert report[field] is False


def test_malformed_probe_policy_fails_validation_without_running_probe(tmp_path):
    tool = load_tool_module()
    policy = load_policy()
    policy["allowed_probe_commands"] = ["rustc --version"]
    policy_path = tmp_path / "malformed-rust-toolchain-readiness.policy.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    calls = []

    report = tool.build_rust_toolchain_readiness_report(
        policy_path=policy_path,
        probe_local_toolchain=True,
        runner=calls.append,
    )

    assert calls == []
    assert report["policy_validation_status"] == "failed"
    assert "allowed_probe_commands must be a list of string lists" in report["policy_validation_errors"]
    assert report["toolchain_probe_status"] == "blocked_by_policy_validation"
    assert report["toolchain_probe_results"] == []
    assert report["safe_to_run_cargo_check_now"] is False


def test_missing_probe_executable_reports_127_without_traceback():
    tool = load_tool_module()

    result = tool.run_probe_command(["definitely-missing-meeting-copilot-rust-tool", "--version"])

    assert result == {
        "command": ["definitely-missing-meeting-copilot-rust-tool", "--version"],
        "returncode": 127,
        "stdout": "",
        "stderr": "missing executable: definitely-missing-meeting-copilot-rust-tool",
    }
