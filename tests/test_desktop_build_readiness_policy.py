import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = REPO_ROOT / "code" / "desktop_tauri" / "build-readiness.policy.json"
TOOL_PATH = REPO_ROOT / "tools" / "desktop_build_readiness.py"


def load_tool_module():
    spec = importlib.util.spec_from_file_location("desktop_build_readiness", TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_policy() -> dict:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def test_build_readiness_policy_exists_and_blocks_build_by_default():
    policy = load_policy()

    assert policy["pcweb_id"] == "PCWEB-083"
    assert policy["policy_status"] == "build_readiness_policy_only"
    assert policy["default_quality_gate_status"] == "included_in_root_pytest"
    assert policy["dependency_install_status"] == "not_run"
    assert policy["build_execution_status"] == "not_run"
    assert policy["tauri_cli_execution_status"] == "not_run"
    assert policy["safe_to_probe_toolchain_versions_now"] is True
    assert policy["safe_to_run_cargo_check_now"] is False
    assert policy["safe_to_run_tauri_dev_now"] is False
    assert policy["safe_to_run_tauri_build_now"] is False
    assert policy["safe_to_install_dependencies_now"] is False
    assert policy["safe_to_generate_lockfiles_now"] is False
    assert policy["safe_to_generate_build_artifacts_now"] is False


def test_build_readiness_policy_limits_probe_commands_and_forbids_build_commands():
    policy = load_policy()

    assert policy["allowed_probe_commands"] == [
        ["rustc", "--version"],
        ["cargo", "--version"],
    ]
    forbidden_commands = {" ".join(command) for command in policy["forbidden_commands"]}
    assert {
        "cargo check",
        "cargo build",
        "cargo tauri dev",
        "cargo tauri build",
        "npm install",
        "pnpm install",
        "yarn install",
        "npm ci",
        "pnpm run tauri dev",
        "pnpm run tauri build",
        "yarn tauri dev",
        "yarn tauri build",
        "npx tauri dev",
        "npx tauri build",
        "npm run tauri dev",
        "npm run tauri build",
    }.issubset(forbidden_commands)

    forbidden_artifacts = set(policy["forbidden_default_artifacts"])
    assert {
        "code/desktop_tauri/src-tauri/Cargo.lock",
        "code/desktop_tauri/src-tauri/target",
        "code/desktop_tauri/package.json",
        "code/desktop_tauri/node_modules",
        "code/desktop_tauri/dist",
    }.issubset(forbidden_artifacts)


def test_build_readiness_policy_locks_forbidden_side_effects_into_report():
    tool = load_tool_module()
    policy = load_policy()

    expected_side_effects = {
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
    }

    assert expected_side_effects.issubset(policy["forbidden_default_side_effects"])

    report = tool.build_readiness_report(policy_path=POLICY_PATH)

    assert expected_side_effects.issubset(report["forbidden_default_side_effects"])


def test_build_readiness_policy_records_future_preconditions_and_sources():
    policy = load_policy()

    assert policy["future_build_check_candidate"] == [
        "cargo",
        "check",
        "--manifest-path",
        "code/desktop_tauri/src-tauri/Cargo.toml",
    ]
    assert policy["future_build_check_status"] == "blocked_until_preconditions_pass"
    assert set(policy["required_future_preconditions"]) == {
        "explicit_user_approval_for_build_artifacts",
        "cargo_lock_policy_decided",
        "target_dir_policy_decided",
        "network_dependency_fetch_policy_decided",
        "cache_cleanup_policy_decided",
        "no_audio_worker_secret_remote_boundary_reconfirmed",
    }
    source_urls = {source["url"] for source in policy["official_sources"]}
    assert "https://v2.tauri.app/start/prerequisites/" in source_urls
    assert "https://v2.tauri.app/reference/cli/" in source_urls
    assert "https://v2.tauri.app/develop/configuration-files/" in source_urls


def test_build_readiness_report_is_static_by_default_and_does_not_probe_toolchain():
    tool = load_tool_module()
    calls = []

    report = tool.build_readiness_report(policy_path=POLICY_PATH, probe_toolchain=False, runner=calls.append)

    assert calls == []
    assert report["pcweb_id"] == "PCWEB-083"
    assert report["report_mode"] == "static_policy_only"
    assert report["toolchain_probe_status"] == "not_run"
    assert report["build_execution_status"] == "not_run"
    assert report["safe_to_run_cargo_check_now"] is False
    assert report["safe_to_run_tauri_dev_now"] is False
    assert report["safe_to_install_dependencies_now"] is False


def test_optional_toolchain_probe_runs_only_version_commands_with_fake_runner():
    tool = load_tool_module()
    calls = []

    def fake_runner(command):
        calls.append(command)
        return {
            "command": command,
            "returncode": 0,
            "stdout": f"{command[0]} 1.0.0",
            "stderr": "",
        }

    report = tool.build_readiness_report(policy_path=POLICY_PATH, probe_toolchain=True, runner=fake_runner)

    assert calls == [["rustc", "--version"], ["cargo", "--version"]]
    assert report["report_mode"] == "toolchain_version_probe_only"
    assert report["toolchain_probe_status"] == "probed"
    assert [result["command"] for result in report["toolchain_probe_results"]] == calls
    assert report["build_execution_status"] == "not_run"
    assert report["dependency_install_status"] == "not_run"
    assert report["tauri_cli_execution_status"] == "not_run"


def test_custom_policy_cannot_expand_executable_probe_allowlist(tmp_path):
    tool = load_tool_module()
    custom_policy = load_policy()
    custom_policy["allowed_probe_commands"] = [
        ["rustc", "--version"],
        ["cargo", "check"],
    ]
    custom_policy_path = tmp_path / "malicious-build-readiness.policy.json"
    custom_policy_path.write_text(json.dumps(custom_policy), encoding="utf-8")
    calls = []

    def fake_runner(command):
        calls.append(command)
        return {
            "command": command,
            "returncode": 0,
            "stdout": f"{command[0]} 1.0.0",
            "stderr": "",
        }

    report = tool.build_readiness_report(
        policy_path=custom_policy_path,
        probe_toolchain=True,
        runner=fake_runner,
    )

    assert calls == [["rustc", "--version"]]
    assert report["toolchain_probe_results"] == [
        {
            "command": ["rustc", "--version"],
            "returncode": 0,
            "stdout": "rustc 1.0.0",
            "stderr": "",
        },
        {
            "command": ["cargo", "check"],
            "returncode": 126,
            "stdout": "",
            "stderr": "blocked by desktop build readiness probe allowlist",
        },
    ]
    assert report["safe_to_run_cargo_check_now"] is False


def test_version_probe_reports_missing_executable_without_traceback():
    tool = load_tool_module()

    result = tool.run_version_command(["definitely-missing-meeting-copilot-tool", "--version"])

    assert result == {
        "command": ["definitely-missing-meeting-copilot-tool", "--version"],
        "returncode": 127,
        "stdout": "",
        "stderr": "missing executable: definitely-missing-meeting-copilot-tool",
    }
