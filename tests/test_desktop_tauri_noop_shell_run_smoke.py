import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DESKTOP_ROOT = REPO_ROOT / "code" / "desktop_tauri"
POLICY_PATH = DESKTOP_ROOT / "tauri-noop-shell-run-smoke.policy.json"
TAURI_CONFIG_PATH = DESKTOP_ROOT / "src-tauri" / "tauri.conf.json"
CAPABILITY_PATH = DESKTOP_ROOT / "src-tauri" / "capabilities" / "default.json"
LIB_RS_PATH = DESKTOP_ROOT / "src-tauri" / "src" / "lib.rs"
CARGO_CHECK_BOUNDARY_POLICY_PATH = DESKTOP_ROOT / "first-cargo-check-execution.policy.json"
TOOL_PATH = REPO_ROOT / "tools" / "desktop_tauri_noop_shell_run_smoke.py"

EXPECTED_NOOP_COMMANDS = [
    "runtime_get_status",
    "session_prepare",
    "asr_worker_health",
    "mic_adapter_prepare",
    "mic_adapter_status",
    "mic_adapter_start",
    "mic_adapter_pause",
    "mic_adapter_resume",
    "mic_adapter_stop",
    "mic_adapter_delete_audio_chunks",
]
EXPECTED_BRIDGE_COMMAND_IDS = [
    "runtime.get_status",
    "session.prepare",
    "asr_worker.health",
    "mic_adapter.prepare",
    "mic_adapter.status",
    "mic_adapter.start",
    "mic_adapter.pause",
    "mic_adapter.resume",
    "mic_adapter.stop",
    "mic_adapter.delete_audio_chunks",
]
EXPECTED_SAFETY_FLAGS = [
    "safe_to_run_tauri_dev_now",
    "safe_to_run_tauri_build_now",
    "safe_to_run_cargo_check_now",
    "safe_to_run_cargo_build_now",
    "safe_to_spawn_process_now",
    "safe_to_fetch_dependencies_now",
    "safe_to_generate_cargo_lock_now",
    "safe_to_generate_target_dir_now",
    "safe_to_generate_installer_now",
    "safe_to_request_audio_permission_now",
    "safe_to_capture_audio_now",
    "safe_to_start_asr_worker_now",
    "safe_to_read_provider_config_now",
    "safe_to_read_secret_now",
    "safe_to_read_configs_local_now",
    "safe_to_call_remote_provider_now",
]


def load_tool_module():
    spec = importlib.util.spec_from_file_location(
        "desktop_tauri_noop_shell_run_smoke",
        TOOL_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_policy() -> dict:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def test_tauri_noop_shell_run_smoke_policy_exists_and_keeps_run_blocked():
    policy = load_policy()

    assert policy["pcweb_id"] == "PCWEB-091"
    assert policy["policy_name"] == "Desktop Tauri No-op Shell Local Run Smoke"
    assert policy["policy_status"] == "tauri_noop_shell_local_run_smoke_policy_only"
    assert policy["default_quality_gate_status"] == "included_in_root_pytest"
    assert policy["smoke_boundary_mode"] == "readiness_report_only"
    assert policy["accepted_desktop_scaffold_source"] == "pcweb_082_tauri_shell_scaffold"
    assert policy["accepted_cargo_check_boundary_source"] == "pcweb_090_first_cargo_check_execution_boundary"
    assert policy["tauri_shell_run_status"] == "not_run"
    assert policy["external_command_execution_status"] == "not_run"
    assert policy["approval_status"] == "explicit_tauri_run_approval_not_recorded"
    assert policy["dev_url"] == "http://127.0.0.1:8765/"
    assert policy["frontend_dist"] == "../../web_mvp/backend/meeting_copilot_web_mvp/frontend_static"
    assert policy["expected_noop_commands"] == EXPECTED_NOOP_COMMANDS
    assert policy["expected_bridge_command_ids"] == EXPECTED_BRIDGE_COMMAND_IDS
    assert policy["bundle_active"] is False
    assert policy["with_global_tauri"] is True
    assert policy["minimal_capability_permissions"] == ["core:default"]
    assert policy["generated_artifact_blockers"] == [
        "package.json",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "node_modules",
        "target",
        "dist",
        "bundle",
        ".dmg",
        ".pkg",
        ".msi",
        ".exe",
        ".app",
    ]
    for flag in EXPECTED_SAFETY_FLAGS:
        assert policy[flag] is False


def test_tauri_noop_shell_run_smoke_tool_source_forbids_command_execution_entrypoints():
    source = TOOL_PATH.read_text(encoding="utf-8")

    forbidden_snippets = [
        "subprocess",
        "os.system",
        "Popen",
        "check_call",
        "check_output",
        "Command::new",
        "std::process",
        "npm install",
        "pnpm install",
        "yarn install",
        "npx ",
    ]
    for snippet in forbidden_snippets:
        assert snippet not in source
    assert "EXTERNAL_COMMAND_EXECUTION_FORBIDDEN = True" in source


def test_valid_scaffold_generates_manual_smoke_packet_but_does_not_run_tauri():
    tool = load_tool_module()

    report = tool.build_tauri_noop_shell_run_smoke_report(
        policy_path=POLICY_PATH,
        tauri_config_path=TAURI_CONFIG_PATH,
        capability_path=CAPABILITY_PATH,
        lib_rs_path=LIB_RS_PATH,
        cargo_check_boundary_policy_path=CARGO_CHECK_BOUNDARY_POLICY_PATH,
        desktop_root=DESKTOP_ROOT,
    )

    assert report["pcweb_id"] == "PCWEB-091"
    assert report["report_mode"] == "tauri_noop_shell_local_run_smoke_static_report"
    assert report["policy_validation_status"] == "passed"
    assert report["tauri_config_validation_status"] == "passed"
    assert report["capability_validation_status"] == "passed"
    assert report["noop_command_validation_status"] == "passed"
    assert report["generated_artifact_validation_status"] == "passed"
    assert report["cargo_check_boundary_validation_status"] == "passed"
    assert report["smoke_packet_status"] == "ready_for_explicit_tauri_run_approval"
    assert report["tauri_shell_run_status"] == "not_run"
    assert report["external_command_execution_status"] == "not_run"
    assert report["manual_smoke_packet"] == {
        "packet_status": "ready_for_manual_review",
        "dev_url": "http://127.0.0.1:8765/",
        "frontend_dist": "../../web_mvp/backend/meeting_copilot_web_mvp/frontend_static",
        "expected_noop_commands": EXPECTED_NOOP_COMMANDS,
        "expected_bridge_command_ids": EXPECTED_BRIDGE_COMMAND_IDS,
        "approval_required": True,
        "command_must_be_run_by": "user_or_separately_approved_runner",
        "post_run_required_action": "record_noop_ipc_result_without_raw_paths_or_secrets",
    }
    for flag in EXPECTED_SAFETY_FLAGS:
        assert report[flag] is False


def test_tauri_config_dev_url_drift_blocks_smoke_packet(tmp_path):
    tool = load_tool_module()
    config = json.loads(TAURI_CONFIG_PATH.read_text(encoding="utf-8"))
    config["build"]["devUrl"] = "http://localhost:3000/"
    config_path = tmp_path / "tauri.conf.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    report = tool.build_tauri_noop_shell_run_smoke_report(
        policy_path=POLICY_PATH,
        tauri_config_path=config_path,
        capability_path=CAPABILITY_PATH,
        lib_rs_path=LIB_RS_PATH,
        cargo_check_boundary_policy_path=CARGO_CHECK_BOUNDARY_POLICY_PATH,
        desktop_root=DESKTOP_ROOT,
    )

    assert report["tauri_config_validation_status"] == "failed"
    assert "build.devUrl must be http://127.0.0.1:8765/" in report["tauri_config_validation_errors"]
    assert report["smoke_packet_status"] == "blocked_by_tauri_config_validation"
    assert report["manual_smoke_packet"]["packet_status"] == "not_generated"


def test_tauri_config_bundle_active_blocks_smoke_packet(tmp_path):
    tool = load_tool_module()
    config = json.loads(TAURI_CONFIG_PATH.read_text(encoding="utf-8"))
    config["bundle"]["active"] = True
    config_path = tmp_path / "tauri.conf.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    report = tool.build_tauri_noop_shell_run_smoke_report(
        policy_path=POLICY_PATH,
        tauri_config_path=config_path,
        capability_path=CAPABILITY_PATH,
        lib_rs_path=LIB_RS_PATH,
        cargo_check_boundary_policy_path=CARGO_CHECK_BOUNDARY_POLICY_PATH,
        desktop_root=DESKTOP_ROOT,
    )

    assert report["tauri_config_validation_status"] == "failed"
    assert "bundle.active must be false" in report["tauri_config_validation_errors"]
    assert report["smoke_packet_status"] == "blocked_by_tauri_config_validation"


def test_tauri_config_without_global_tauri_blocks_smoke_packet(tmp_path):
    tool = load_tool_module()
    config = json.loads(TAURI_CONFIG_PATH.read_text(encoding="utf-8"))
    config["app"].pop("withGlobalTauri", None)
    config_path = tmp_path / "tauri.conf.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    report = tool.build_tauri_noop_shell_run_smoke_report(
        policy_path=POLICY_PATH,
        tauri_config_path=config_path,
        capability_path=CAPABILITY_PATH,
        lib_rs_path=LIB_RS_PATH,
        cargo_check_boundary_policy_path=CARGO_CHECK_BOUNDARY_POLICY_PATH,
        desktop_root=DESKTOP_ROOT,
    )

    assert report["tauri_config_validation_status"] == "failed"
    assert "app.withGlobalTauri must be true for window.__TAURI__ no-op IPC collector" in report[
        "tauri_config_validation_errors"
    ]
    assert report["smoke_packet_status"] == "blocked_by_tauri_config_validation"


def test_capability_permission_drift_blocks_smoke_packet(tmp_path):
    tool = load_tool_module()
    capability = json.loads(CAPABILITY_PATH.read_text(encoding="utf-8"))
    capability["permissions"] = ["core:default", "shell:allow-open"]
    capability_path = tmp_path / "default.json"
    capability_path.write_text(json.dumps(capability), encoding="utf-8")

    report = tool.build_tauri_noop_shell_run_smoke_report(
        policy_path=POLICY_PATH,
        tauri_config_path=TAURI_CONFIG_PATH,
        capability_path=capability_path,
        lib_rs_path=LIB_RS_PATH,
        cargo_check_boundary_policy_path=CARGO_CHECK_BOUNDARY_POLICY_PATH,
        desktop_root=DESKTOP_ROOT,
    )

    assert report["capability_validation_status"] == "failed"
    assert "capability.permissions must remain ['core:default']" in report["capability_validation_errors"]
    assert report["smoke_packet_status"] == "blocked_by_capability_validation"


def test_extra_noop_command_blocks_smoke_packet(tmp_path):
    tool = load_tool_module()
    lib_rs = LIB_RS_PATH.read_text(encoding="utf-8")
    lib_rs = lib_rs.replace(
        "fn asr_worker_health() -> NoopBridgeResponse {",
        "#[tauri::command]\nfn audio_capture_start() -> NoopBridgeResponse {\n"
        "    NoopBridgeResponse::for_command(\"audio.capture_start\")\n"
        "}\n\nfn asr_worker_health() -> NoopBridgeResponse {",
    )
    lib_rs = lib_rs.replace(
        "asr_worker_health\n        ])",
        "asr_worker_health,\n            audio_capture_start\n        ])",
    )
    lib_path = tmp_path / "lib.rs"
    lib_path.write_text(lib_rs, encoding="utf-8")

    report = tool.build_tauri_noop_shell_run_smoke_report(
        policy_path=POLICY_PATH,
        tauri_config_path=TAURI_CONFIG_PATH,
        capability_path=CAPABILITY_PATH,
        lib_rs_path=lib_path,
        cargo_check_boundary_policy_path=CARGO_CHECK_BOUNDARY_POLICY_PATH,
        desktop_root=DESKTOP_ROOT,
    )

    assert report["noop_command_validation_status"] == "failed"
    assert "noop command functions must match PCWEB-107 catalog" in report[
        "noop_command_validation_errors"
    ]
    assert "bridge command ids must match PCWEB-107 catalog" in report[
        "noop_command_validation_errors"
    ]
    assert report["smoke_packet_status"] == "blocked_by_noop_command_validation"


def test_swapped_noop_command_mapping_blocks_smoke_packet(tmp_path):
    tool = load_tool_module()
    lib_rs = LIB_RS_PATH.read_text(encoding="utf-8")
    lib_rs = lib_rs.replace(
        'fn runtime_get_status() -> NoopBridgeResponse {\n'
        '    NoopBridgeResponse::for_command("runtime.get_status")\n'
        '}',
        'fn runtime_get_status() -> NoopBridgeResponse {\n'
        '    NoopBridgeResponse::for_command("session.prepare")\n'
        '}',
    )
    lib_rs = lib_rs.replace(
        'fn session_prepare() -> NoopBridgeResponse {\n'
        '    NoopBridgeResponse::for_command("session.prepare")\n'
        '}',
        'fn session_prepare() -> NoopBridgeResponse {\n'
        '    NoopBridgeResponse::for_command("runtime.get_status")\n'
        '}',
    )
    lib_path = tmp_path / "lib.rs"
    lib_path.write_text(lib_rs, encoding="utf-8")

    report = tool.build_tauri_noop_shell_run_smoke_report(
        policy_path=POLICY_PATH,
        tauri_config_path=TAURI_CONFIG_PATH,
        capability_path=CAPABILITY_PATH,
        lib_rs_path=lib_path,
        cargo_check_boundary_policy_path=CARGO_CHECK_BOUNDARY_POLICY_PATH,
        desktop_root=DESKTOP_ROOT,
    )

    assert report["noop_command_validation_status"] == "failed"
    assert "runtime_get_status must return runtime.get_status" in report[
        "noop_command_validation_errors"
    ]
    assert "session_prepare must return session.prepare" in report[
        "noop_command_validation_errors"
    ]
    assert report["smoke_packet_status"] == "blocked_by_noop_command_validation"


def test_swapped_mic_adapter_noop_mapping_blocks_smoke_packet(tmp_path):
    tool = load_tool_module()
    lib_rs = LIB_RS_PATH.read_text(encoding="utf-8")
    lib_rs = lib_rs.replace(
        'fn mic_adapter_start() -> NoopBridgeResponse {\n'
        '    NoopBridgeResponse::for_command("mic_adapter.start")\n'
        '}',
        'fn mic_adapter_start() -> NoopBridgeResponse {\n'
        '    NoopBridgeResponse::for_command("mic_adapter.status")\n'
        '}',
    )
    lib_path = tmp_path / "lib.rs"
    lib_path.write_text(lib_rs, encoding="utf-8")

    report = tool.build_tauri_noop_shell_run_smoke_report(
        policy_path=POLICY_PATH,
        tauri_config_path=TAURI_CONFIG_PATH,
        capability_path=CAPABILITY_PATH,
        lib_rs_path=lib_path,
        cargo_check_boundary_policy_path=CARGO_CHECK_BOUNDARY_POLICY_PATH,
        desktop_root=DESKTOP_ROOT,
    )

    assert report["noop_command_validation_status"] == "failed"
    assert "mic_adapter_start must return mic_adapter.start" in report[
        "noop_command_validation_errors"
    ]
    assert report["smoke_packet_status"] == "blocked_by_noop_command_validation"


def test_noop_response_side_effect_drift_blocks_smoke_packet(tmp_path):
    tool = load_tool_module()
    lib_rs = LIB_RS_PATH.read_text(encoding="utf-8")
    lib_rs = lib_rs.replace("safe_to_execute_real_action: false", "safe_to_execute_real_action: true")
    lib_rs = lib_rs.replace("captures_audio: false", "captures_audio: true")
    lib_path = tmp_path / "lib.rs"
    lib_path.write_text(lib_rs, encoding="utf-8")

    report = tool.build_tauri_noop_shell_run_smoke_report(
        policy_path=POLICY_PATH,
        tauri_config_path=TAURI_CONFIG_PATH,
        capability_path=CAPABILITY_PATH,
        lib_rs_path=lib_path,
        cargo_check_boundary_policy_path=CARGO_CHECK_BOUNDARY_POLICY_PATH,
        desktop_root=DESKTOP_ROOT,
    )

    assert report["noop_command_validation_status"] == "failed"
    assert "safe_to_execute_real_action must remain false" in report[
        "noop_command_validation_errors"
    ]
    assert "captures_audio must remain false" in report["noop_command_validation_errors"]
    assert report["smoke_packet_status"] == "blocked_by_noop_command_validation"


def test_generated_artifact_presence_blocks_smoke_packet(tmp_path):
    tool = load_tool_module()
    desktop_root = tmp_path / "desktop_tauri"
    desktop_root.mkdir()
    (desktop_root / "src-tauri").mkdir()
    (desktop_root / "node_modules").mkdir()
    (desktop_root / "package.json").write_text("{}", encoding="utf-8")
    (desktop_root / "package-lock.json").write_text("{}", encoding="utf-8")
    (desktop_root / "pnpm-lock.yaml").write_text("", encoding="utf-8")
    (desktop_root / "yarn.lock").write_text("", encoding="utf-8")

    report = tool.build_tauri_noop_shell_run_smoke_report(
        policy_path=POLICY_PATH,
        tauri_config_path=TAURI_CONFIG_PATH,
        capability_path=CAPABILITY_PATH,
        lib_rs_path=LIB_RS_PATH,
        cargo_check_boundary_policy_path=CARGO_CHECK_BOUNDARY_POLICY_PATH,
        desktop_root=desktop_root,
    )

    assert report["generated_artifact_validation_status"] == "failed"
    assert "generated artifact present: node_modules" in report[
        "generated_artifact_validation_errors"
    ]
    for artifact_name in ["package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock"]:
        assert f"generated artifact present: {artifact_name}" in report[
            "generated_artifact_validation_errors"
        ]
    assert report["smoke_packet_status"] == "blocked_by_generated_artifact_validation"


def test_pcweb_090_policy_drift_blocks_smoke_packet(tmp_path):
    tool = load_tool_module()
    cargo_boundary = json.loads(CARGO_CHECK_BOUNDARY_POLICY_PATH.read_text(encoding="utf-8"))
    cargo_boundary["safe_to_run_tauri_dev_now"] = True
    boundary_path = tmp_path / "first-cargo-check-execution.policy.json"
    boundary_path.write_text(json.dumps(cargo_boundary), encoding="utf-8")

    report = tool.build_tauri_noop_shell_run_smoke_report(
        policy_path=POLICY_PATH,
        tauri_config_path=TAURI_CONFIG_PATH,
        capability_path=CAPABILITY_PATH,
        lib_rs_path=LIB_RS_PATH,
        cargo_check_boundary_policy_path=boundary_path,
        desktop_root=DESKTOP_ROOT,
    )

    assert report["cargo_check_boundary_validation_status"] == "failed"
    assert "pcweb_090 safe_to_run_tauri_dev_now must be false" in report[
        "cargo_check_boundary_validation_errors"
    ]
    assert report["smoke_packet_status"] == "blocked_by_cargo_check_boundary_validation"


def test_forbidden_paths_are_blocked_before_read_for_all_inputs(tmp_path):
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

        report = tool.build_tauri_noop_shell_run_smoke_report(
            policy_path=blocked_path,
            tauri_config_path=TAURI_CONFIG_PATH,
            capability_path=CAPABILITY_PATH,
            lib_rs_path=LIB_RS_PATH,
            cargo_check_boundary_policy_path=CARGO_CHECK_BOUNDARY_POLICY_PATH,
            desktop_root=DESKTOP_ROOT,
        )
        assert report["policy_read_status"] == "blocked"
        assert f"path is blocked: {expected_label}" in report["policy_validation_errors"]

        report = tool.build_tauri_noop_shell_run_smoke_report(
            policy_path=POLICY_PATH,
            tauri_config_path=blocked_path,
            capability_path=CAPABILITY_PATH,
            lib_rs_path=LIB_RS_PATH,
            cargo_check_boundary_policy_path=CARGO_CHECK_BOUNDARY_POLICY_PATH,
            desktop_root=DESKTOP_ROOT,
        )
        assert report["tauri_config_read_status"] == "blocked"
        assert f"path is blocked: {expected_label}" in report["tauri_config_validation_errors"]

        report = tool.build_tauri_noop_shell_run_smoke_report(
            policy_path=POLICY_PATH,
            tauri_config_path=TAURI_CONFIG_PATH,
            capability_path=blocked_path,
            lib_rs_path=LIB_RS_PATH,
            cargo_check_boundary_policy_path=CARGO_CHECK_BOUNDARY_POLICY_PATH,
            desktop_root=DESKTOP_ROOT,
        )
        assert report["capability_read_status"] == "blocked"
        assert f"path is blocked: {expected_label}" in report["capability_validation_errors"]

        report = tool.build_tauri_noop_shell_run_smoke_report(
            policy_path=POLICY_PATH,
            tauri_config_path=TAURI_CONFIG_PATH,
            capability_path=CAPABILITY_PATH,
            lib_rs_path=blocked_path,
            cargo_check_boundary_policy_path=CARGO_CHECK_BOUNDARY_POLICY_PATH,
            desktop_root=DESKTOP_ROOT,
        )
        assert report["noop_command_read_status"] == "blocked"
        assert f"path is blocked: {expected_label}" in report["noop_command_validation_errors"]

        report = tool.build_tauri_noop_shell_run_smoke_report(
            policy_path=POLICY_PATH,
            tauri_config_path=TAURI_CONFIG_PATH,
            capability_path=CAPABILITY_PATH,
            lib_rs_path=LIB_RS_PATH,
            cargo_check_boundary_policy_path=blocked_path,
            desktop_root=DESKTOP_ROOT,
        )
        assert report["cargo_check_boundary_read_status"] == "blocked"
        assert f"path is blocked: {expected_label}" in report[
            "cargo_check_boundary_validation_errors"
        ]
