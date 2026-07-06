import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = REPO_ROOT / "code" / "desktop_tauri" / "cargo-check.policy.json"
TOOL_PATH = REPO_ROOT / "tools" / "desktop_cargo_check_policy.py"


def load_tool_module():
    spec = importlib.util.spec_from_file_location("desktop_cargo_check_policy", TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_policy() -> dict:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def test_cargo_check_policy_exists_and_keeps_execution_blocked():
    policy = load_policy()

    assert policy["pcweb_id"] == "PCWEB-084"
    assert policy["policy_status"] == "cargo_check_artifact_policy_only"
    assert policy["default_quality_gate_status"] == "included_in_root_pytest"
    assert policy["cargo_check_execution_status"] == "not_run"
    assert policy["safe_to_run_cargo_check_now"] is False
    assert policy["safe_to_install_toolchain_now"] is False
    assert policy["safe_to_fetch_dependencies_now"] is False
    assert policy["safe_to_generate_cargo_lock_now"] is False
    assert policy["safe_to_generate_target_dir_now"] is False
    assert policy["safe_to_run_tauri_dev_now"] is False
    assert policy["safe_to_run_tauri_build_now"] is False
    assert policy["safe_to_read_configs_local_now"] is False


def test_cargo_check_policy_tool_source_forbids_external_execution_entrypoints():
    source = TOOL_PATH.read_text(encoding="utf-8")

    assert "subprocess" not in source
    assert "os.system" not in source
    assert "Popen" not in source
    assert "check_call" not in source
    assert "check_output" not in source
    assert "EXTERNAL_COMMAND_EXECUTION_FORBIDDEN = True" in source


def test_policy_records_future_commands_environment_and_artifact_paths():
    policy = load_policy()

    assert policy["cargo_lock_policy"]["status"] == "generated_after_pcweb_118_controlled_check"
    assert policy["cargo_lock_policy"]["path"] == "code/desktop_tauri/src-tauri/Cargo.lock"
    assert policy["cargo_lock_policy"]["post_approval_action"] == "keep_committed_for_desktop_app_reproducibility"
    assert policy["cargo_target_dir_policy"]["status"] == "created_under_ignored_artifacts_after_pcweb_118_controlled_check"
    assert policy["cargo_target_dir_policy"]["path"] == "artifacts/tmp/desktop_tauri_target"
    assert policy["cargo_target_dir_policy"]["must_be_under_ignored_path"] is True
    assert policy["future_first_approved_cargo_check"]["command"] == [
        "cargo",
        "check",
        "--manifest-path",
        "code/desktop_tauri/src-tauri/Cargo.toml",
    ]
    assert policy["future_first_approved_cargo_check"]["env"] == {
        "CARGO_TARGET_DIR": "artifacts/tmp/desktop_tauri_target",
    }
    assert policy["future_repeat_locked_offline_cargo_check"]["command"] == [
        "cargo",
        "check",
        "--manifest-path",
        "code/desktop_tauri/src-tauri/Cargo.toml",
        "--locked",
        "--offline",
    ]
    assert policy["future_repeat_locked_offline_cargo_check"]["env"] == {
        "CARGO_TARGET_DIR": "artifacts/tmp/desktop_tauri_target",
    }
    assert set(policy["allowed_future_artifacts_after_explicit_approval"]) == {
        "code/desktop_tauri/src-tauri/Cargo.lock",
        "artifacts/tmp/desktop_tauri_target",
    }
    assert {
        "code/desktop_tauri/src-tauri/target",
        "code/desktop_tauri/node_modules",
        "code/desktop_tauri/dist",
        "code/desktop_tauri/bundle",
    }.issubset(policy["forbidden_source_tree_artifacts"])


def test_policy_records_network_cleanup_and_side_effect_boundaries():
    policy = load_policy()

    assert policy["network_dependency_fetch_policy"]["status"] == "blocked_by_default"
    assert policy["network_dependency_fetch_policy"]["first_approved_fetch_scope"] == "cargo_crates_only"
    assert policy["network_dependency_fetch_policy"]["repeat_check_mode"] == "locked_offline_after_lock_and_cache"
    assert policy["cleanup_policy"]["status"] == "decided_not_executed"
    assert policy["cleanup_policy"]["disposable_paths"] == ["artifacts/tmp/desktop_tauri_target"]
    assert policy["cleanup_policy"]["must_not_remove_paths"] == [
        "code/desktop_tauri/src-tauri/Cargo.toml",
        "code/desktop_tauri/src-tauri/Cargo.lock",
        "code/desktop_tauri/src-tauri/src",
        "code/desktop_tauri/src-tauri/tauri.conf.json",
    ]

    forbidden_commands = {" ".join(command) for command in policy["forbidden_commands"]}
    assert {
        "cargo build",
        "cargo tauri dev",
        "cargo tauri build",
        "npm install",
        "npm ci",
        "pnpm install",
        "yarn install",
        "npx tauri dev",
        "npx tauri build",
    }.issubset(forbidden_commands)

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
        "installer_creation",
        "signing",
        "notarization",
    }.issubset(policy["forbidden_default_side_effects"])

    source_urls = {source["url"] for source in policy["official_sources"]}
    assert "https://doc.rust-lang.org/cargo/commands/cargo-check.html" in source_urls
    assert "https://doc.rust-lang.org/cargo/reference/build-cache.html" in source_urls
    assert "https://doc.rust-lang.org/cargo/reference/environment-variables.html" in source_urls
    assert "https://doc.rust-lang.org/cargo/guide/cargo-toml-vs-cargo-lock.html" in source_urls
    assert "https://v2.tauri.app/start/prerequisites/" in source_urls


def test_cargo_check_policy_report_is_static_and_read_only():
    tool = load_tool_module()

    report = tool.build_cargo_check_policy_report(policy_path=POLICY_PATH)

    assert report["pcweb_id"] == "PCWEB-084"
    assert report["report_mode"] == "cargo_check_policy_static_report"
    assert report["policy_validation_status"] == "passed"
    assert report["cargo_check_execution_status"] == "not_run"
    assert report["safe_to_run_cargo_check_now"] is False
    assert report["external_command_execution_status"] == "not_run"
    assert report["cargo_lock_exists"] is True
    assert report["approved_target_dir_exists"] is True
    assert report["forbidden_in_source_target_dir_exists"] is False
    assert report["first_approved_cargo_check_plan"]["status"] == "executed_once_in_pcweb_118_controlled_check"
    assert report["repeat_locked_offline_check_plan"]["status"] == "candidate_ready_after_pcweb_118_lock_and_cache"


def test_cargo_check_policy_report_detects_artifacts_without_creating_or_deleting(tmp_path):
    tool = load_tool_module()
    policy = load_policy()
    policy_path = tmp_path / "cargo-check.policy.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    cargo_lock = tmp_path / "code" / "desktop_tauri" / "src-tauri" / "Cargo.lock"
    approved_target_dir = tmp_path / "artifacts" / "tmp" / "desktop_tauri_target"
    source_target_dir = tmp_path / "code" / "desktop_tauri" / "src-tauri" / "target"
    cargo_lock.parent.mkdir(parents=True)
    cargo_lock.write_text("# generated later in real flow\n", encoding="utf-8")
    approved_target_dir.mkdir(parents=True)
    source_target_dir.mkdir(parents=True)

    report = tool.build_cargo_check_policy_report(policy_path=policy_path, repo_root=tmp_path)

    assert report["cargo_lock_exists"] is True
    assert report["approved_target_dir_exists"] is True
    assert report["forbidden_in_source_target_dir_exists"] is True
    assert cargo_lock.exists()
    assert approved_target_dir.exists()
    assert source_target_dir.exists()


def test_malformed_policy_shape_fails_validation_without_command_readiness(tmp_path):
    tool = load_tool_module()
    policy = load_policy()
    policy["future_first_approved_cargo_check"]["command"] = "cargo check"
    policy_path = tmp_path / "malformed-cargo-check.policy.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")

    report = tool.build_cargo_check_policy_report(policy_path=policy_path, repo_root=tmp_path)

    assert report["policy_validation_status"] == "failed"
    assert "future_first_approved_cargo_check.command must be a list of strings" in report["policy_validation_errors"]
    assert report["cargo_check_execution_status"] == "not_run"
    assert report["external_command_execution_status"] == "not_run"
    assert report["safe_to_run_cargo_check_now"] is False
    assert report["first_approved_cargo_check_plan"]["status"] == "blocked_by_policy_validation"


def test_policy_records_remaining_preconditions():
    policy = load_policy()

    assert set(policy["remaining_preconditions_before_first_cargo_check"]) == {
        "explicit_user_approval_for_first_cargo_check",
        "rust_toolchain_available",
        "first_dependency_resolution_network_fetch_approved_or_cache_preseeded",
        "cargo_lock_policy_acknowledged",
        "cargo_target_dir_policy_acknowledged",
        "cleanup_policy_acknowledged",
        "no_audio_worker_secret_remote_boundary_reconfirmed",
    }
