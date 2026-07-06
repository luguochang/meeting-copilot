import importlib.util
import io
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_quality_gate_module():
    module_path = REPO_ROOT / "tools" / "run_quality_gate.py"
    spec = importlib.util.spec_from_file_location("run_quality_gate", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_default_pc_web_profile_lists_root_core_backend_and_browser_without_paid_smokes():
    quality_gate = load_quality_gate_module()

    steps = quality_gate.build_steps("pc-web", include_browser=True)

    assert [(step.step_id, step.cwd_relative, step.command) for step in steps] == [
        ("root-pytest", ".", ["python3", "-m", "pytest", "tests", "-q"]),
        ("core-pytest", "code/core", ["python3", "-m", "pytest", "-q"]),
        ("web-backend-pytest", "code/web_mvp/backend", ["python3", "-m", "pytest", "-q"]),
        ("web-browser-smoke", "code/web_mvp", ["node", "e2e/browser_smoke.mjs"]),
    ]
    joined_commands = "\n".join(" ".join(step.command) for step in steps)
    assert "llm_smoke" not in joined_commands
    assert "configs/local" not in joined_commands
    assert "transcribe_funasr" not in joined_commands
    for forbidden_command in [
        "cargo",
        "rustup",
        "rustc",
        "xcode-select",
        "curl",
        "brew",
        "winget",
        "apt",
        "npm",
        "pnpm",
        "yarn",
        "npx",
        "tauri dev",
        "tauri build",
    ]:
        assert forbidden_command not in joined_commands


def test_all_local_profile_adds_asr_tests_without_remote_provider_smokes():
    quality_gate = load_quality_gate_module()

    steps = quality_gate.build_steps("all-local", include_browser=True)

    assert [step.step_id for step in steps] == [
        "asr-runtime-pytest",
        "asr-bakeoff-pytest",
        "root-pytest",
        "core-pytest",
        "web-backend-pytest",
        "web-browser-smoke",
    ]
    joined_commands = "\n".join(" ".join(step.command) for step in steps)
    assert "llm_smoke" not in joined_commands
    assert "configs/local" not in joined_commands
    assert "--llm-config" not in joined_commands
    for forbidden_command in [
        "cargo",
        "rustup",
        "rustc",
        "xcode-select",
        "curl",
        "brew",
        "winget",
        "apt",
        "npm",
        "pnpm",
        "yarn",
        "npx",
        "tauri dev",
        "tauri build",
    ]:
        assert forbidden_command not in joined_commands


def test_no_browser_flag_excludes_browser_smoke_step():
    quality_gate = load_quality_gate_module()

    steps = quality_gate.build_steps("pc-web", include_browser=False)

    assert [step.step_id for step in steps] == [
        "root-pytest",
        "core-pytest",
        "web-backend-pytest",
    ]
    joined_commands = "\n".join(" ".join(step.command) for step in steps)
    assert "browser_smoke" not in joined_commands
    assert "node" not in joined_commands


def test_dry_run_prints_commands_without_executing_steps():
    quality_gate = load_quality_gate_module()
    output = io.StringIO()
    executed = []

    exit_code = quality_gate.run_gate(
        profile="pc-web",
        dry_run=True,
        include_browser=True,
        runner=lambda step: executed.append(step.step_id) or 0,
        out=output,
    )

    assert exit_code == 0
    assert executed == []
    rendered = output.getvalue()
    assert "[dry-run]" in rendered
    assert ". $ python3 -m pytest tests -q" in rendered
    assert "code/core $ python3 -m pytest -q" in rendered
    assert "code/web_mvp $ node e2e/browser_smoke.mjs" in rendered


def test_run_gate_reports_missing_executable_without_traceback():
    quality_gate = load_quality_gate_module()
    output = io.StringIO()
    executed = []

    def runner(step):
        executed.append(step.step_id)
        raise FileNotFoundError(2, "No such file or directory", "python3")

    exit_code = quality_gate.run_gate(
        profile="pc-web",
        dry_run=False,
        include_browser=True,
        runner=runner,
        out=output,
    )

    assert exit_code == 127
    assert executed == ["root-pytest"]
    rendered = output.getvalue()
    assert "[fail] root-pytest could not start missing executable: python3" in rendered
    assert "Traceback" not in rendered


def test_run_gate_stops_on_first_failing_step():
    quality_gate = load_quality_gate_module()
    output = io.StringIO()
    executed = []

    def runner(step):
        executed.append(step.step_id)
        if step.step_id == "web-backend-pytest":
            return 7
        return 0

    exit_code = quality_gate.run_gate(
        profile="pc-web",
        dry_run=False,
        include_browser=True,
        runner=runner,
        out=output,
    )

    assert exit_code == 7
    assert executed == ["root-pytest", "core-pytest", "web-backend-pytest"]
    assert "web-backend-pytest failed with exit code 7" in output.getvalue()


def test_run_gate_flushes_step_announcement_before_executing_runner():
    quality_gate = load_quality_gate_module()
    output = io.StringIO()
    flushed_before_runner = []

    class FlushTrackingOutput:
        def write(self, value):
            return output.write(value)

        def flush(self):
            flushed_before_runner.append(output.getvalue())

    def runner(step):
        if step.step_id == "root-pytest":
            assert any("root-pytest: . $ python3 -m pytest tests -q" in text for text in flushed_before_runner)
        return 0

    exit_code = quality_gate.run_gate(
        profile="pc-web",
        dry_run=False,
        include_browser=False,
        runner=runner,
        out=FlushTrackingOutput(),
    )

    assert exit_code == 0
