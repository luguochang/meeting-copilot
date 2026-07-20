import importlib.util
import json
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "macos_bundled_runtime_spike.py"


def load_tool_module():
    spec = importlib.util.spec_from_file_location("macos_bundled_runtime_spike", TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_output_root_and_run_id_are_bounded(tmp_path):
    tool = load_tool_module()
    repo = tmp_path / "repo"
    repo.mkdir()

    assert tool.resolve_output_root(repo, repo / "artifacts/tmp/bundle") == repo / "artifacts/tmp/bundle"
    with pytest.raises(ValueError, match="artifacts/tmp"):
        tool.resolve_output_root(repo, tmp_path / "outside")
    with pytest.raises(ValueError, match="run_id"):
        tool.validate_run_id("../escape")


def test_runtime_manifest_is_the_single_source_of_python_and_inventory_paths():
    tool = load_tool_module()

    manifest = tool.load_runtime_manifest(REPO_ROOT)

    assert manifest["schema_version"] == "meeting_copilot.runtime_bundle.v1"
    assert manifest["runtimes"]["backend"]["python_version"] == "3.13"
    assert manifest["runtimes"]["funasr"]["python_version"] == "3.11"
    assert "source_venv" not in json.dumps(manifest)
    assert manifest["app_identity"]["bundle_identifier"] == "com.meetingcopilot.desktop"
    assert manifest["runtimes"]["backend"]["executable"] in manifest["required_files"]
    assert manifest["runtimes"]["funasr"]["executable"] in manifest["required_files"]
    assert "models/funasr-online/model.pt" in manifest["required_files"]
    assert "bin/meeting-copilot-native-mic" in manifest["required_files"]
    assert manifest["workers"]["file_asr"] in manifest["required_files"]


def test_backend_probe_startup_budget_matches_packaged_supervisor_budget():
    tool = load_tool_module()

    assert tool.BACKEND_STARTUP_TIMEOUT_SECONDS == 60.0
    assert tool.BACKEND_PROBE_REQUEST_TIMEOUT_SECONDS == 5.0


def test_backend_probe_request_budget_allows_cold_provider_capability_probe(monkeypatch):
    tool = load_tool_module()
    observed: dict[str, object] = {}

    class Response:
        status = 200

        @staticmethod
        def read(_limit):
            return b'{"status":"ok"}'

    class Connection:
        def __init__(self, host, port, timeout):
            observed.update(host=host, port=port, timeout=timeout)

        def request(self, method, path):
            observed.update(method=method, path=path)

        @staticmethod
        def getresponse():
            return Response()

        @staticmethod
        def close():
            return None

    monkeypatch.setattr(tool.http.client, "HTTPConnection", Connection)

    result = tool.http_get(8765, "/providers/health")

    assert result["status_code"] == 200
    assert observed == {
        "host": "127.0.0.1",
        "port": 8765,
        "timeout": 5.0,
        "method": "GET",
        "path": "/providers/health",
    }


def test_native_mic_helper_contract_is_native_and_bundle_relocatable():
    tool = load_tool_module()

    command = tool.native_mic_build_command(REPO_ROOT, Path("/tmp/MeetingCopilotRuntime.bundle"))
    source = (REPO_ROOT / tool.NATIVE_MIC_SOURCE_RELATIVE).read_text(encoding="utf-8")

    assert command[0:2] == ["xcrun", "swiftc"]
    assert command[-1].endswith("bin/meeting-copilot-native-mic")
    assert "AVAudioEngine" in source
    assert "AVAudioConverter" in source
    assert "URLSessionWebSocketTask" in source
    assert '"END"' in source
    assert "16_000" in source
    assert "frameSamples = 4_800" in source
    assert "pendingPCM" in source
    assert 'nativePCMProtocolName = "native_pcm_v2"' in source
    assert "nativePCMEnvelope(" in source
    assert "captureEpoch: configuration.captureEpoch" in source
    assert "task.send(.data(envelope))" in source
    assert 'components.host == "127.0.0.1"' in source
    assert "API_KEY" not in source
    assert "MEETING_COPILOT_SESSION_COOKIE" in source
    assert 'case "--cookie"' not in source


def test_native_mic_main_loop_is_owned_by_the_synchronous_process_entry():
    tool = load_tool_module()
    source = (REPO_ROOT / tool.NATIVE_MIC_SOURCE_RELATIVE).read_text(encoding="utf-8")

    assert "static func main() {" in source
    assert "static func main() async" not in source
    assert source.index("Task {") < source.index("dispatchMain()")
    assert "task.sendPing" in source
    assert "timeoutIntervalForResource" in source


def test_native_mic_probe_accepts_help_written_to_stderr(tmp_path):
    tool = load_tool_module()
    helper = tmp_path / "bin" / "meeting-copilot-native-mic"
    helper.parent.mkdir(parents=True)
    helper.write_text(
        "#!/bin/sh\necho 'Usage: meeting-copilot-native-mic --help' >&2\n",
        encoding="utf-8",
    )
    helper.chmod(helper.stat().st_mode | 0o700)

    probe = tool.probe_native_mic(tmp_path)

    assert probe["status"] == "passed"
    assert probe["return_code"] == 0


def test_missing_runtime_preconditions_fail_before_python_execution(tmp_path, monkeypatch):
    tool = load_tool_module()
    repo = tmp_path / "repo"
    (repo / "artifacts/tmp").mkdir(parents=True)
    (repo / "code/desktop_tauri").mkdir(parents=True)
    (repo / "code/desktop_tauri/runtime-bundle-manifest.json").write_text(
        (REPO_ROOT / "code/desktop_tauri/runtime-bundle-manifest.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    calls = []
    monkeypatch.setattr(tool, "python_runtime_info", lambda path: calls.append(path))

    with pytest.raises(RuntimeError, match="bundle preconditions failed"):
        tool.build_and_probe(
            repo_root=repo,
            output_root=repo / "artifacts/tmp/runtime",
            run_id="missing-runtime",
            model_dir=repo / "missing-model",
        )

    assert calls == []


def test_rewrite_venv_uses_only_relative_bundle_python_links(tmp_path):
    tool = load_tool_module()
    venv = tmp_path / "runtime" / "backend-venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").symlink_to("/Users/example/.local/bin/python3.12")
    (venv / "bin" / "python3").symlink_to("python")
    (venv / "bin" / "python3.12").symlink_to("/Users/example/.local/bin/python3.12")
    (venv / "pyvenv.cfg").write_text("home = /Users/example/.local/bin\n", encoding="utf-8")

    tool.rewrite_venv(venv, python_directory_name="backend-python", version="3.12")

    assert (venv / "bin" / "python").readlink() == Path("../../backend-python/bin/python3.12")
    assert (venv / "bin" / "python3").readlink() == Path("python")
    assert (venv / "bin" / "python3.12").readlink() == Path("python")
    config = (venv / "pyvenv.cfg").read_text(encoding="utf-8")
    assert "home = ../../backend-python/bin" in config
    assert "/Users/example" not in config


def test_clean_probe_environment_drops_parent_secrets_and_binds_bundle_paths(tmp_path, monkeypatch):
    tool = load_tool_module()
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-parent-secret")
    monkeypatch.setenv("PYTHONPATH", "/private/source-tree")
    bundle = tmp_path / "MeetingCopilotRuntime.bundle"
    probe_root = tmp_path / "probe"
    bundle.mkdir()
    (bundle / "runtime-bundle-manifest.json").write_text(
        (REPO_ROOT / "code/desktop_tauri/runtime-bundle-manifest.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    environment = tool.clean_probe_environment(bundle, probe_root)

    serialized = json.dumps(environment)
    assert "sk-parent-secret" not in serialized
    assert "/private/source-tree" not in serialized
    assert environment["PYTHONHOME"] == str(bundle / "runtime/backend-python")
    assert environment["PYTHONPATH"].split(":") == [
        str(bundle / "runtime/backend-venv/lib/python3.13/site-packages"),
        str(bundle / "app/code/web_mvp/backend"),
        str(bundle / "app/code/core"),
    ]
    assert environment["MEETING_COPILOT_FUNASR_PYTHON"] == str(
        bundle / "runtime/funasr-python/bin/python3.11"
    )
    assert environment["MEETING_COPILOT_FUNASR_MODEL_DIR"] == str(bundle / "models/funasr-online")
    assert environment["MEETING_COPILOT_RUNTIME_MANIFEST"] == str(
        bundle / "runtime-bundle-manifest.json"
    )
    assert environment["MEETING_COPILOT_DIARIZATION_VAD_DIR"] == str(
        bundle / "models/diarization-vad"
    )
    assert environment["MEETING_COPILOT_DIARIZATION_CAMPLUS_DIR"] == str(
        bundle / "models/diarization-camplus"
    )
    assert environment["HF_HUB_OFFLINE"] == "1"


def test_external_symlink_scan_fails_closed(tmp_path):
    tool = load_tool_module()
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "inside.txt").write_text("inside", encoding="utf-8")
    (bundle / "relative-link").symlink_to("inside.txt")
    (bundle / "external-link").symlink_to(tmp_path / "outside.txt")

    assert tool.external_symlinks(bundle) == ["external-link"]


def test_launchers_are_relocatable_and_do_not_embed_repository_path(tmp_path):
    tool = load_tool_module()
    bundle = tmp_path / "bundle"

    tool.write_launchers(bundle, manifest=tool.load_runtime_manifest(REPO_ROOT))

    backend = (bundle / "bin" / "meeting-copilot-backend").read_text(encoding="utf-8")
    worker = (bundle / "bin" / "meeting-copilot-asr-worker").read_text(encoding="utf-8")
    assert 'ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"' in backend
    assert "Documents/面试" not in backend + worker
    assert "export PYTHONDONTWRITEBYTECODE=1" in backend
    assert "export PYTHONDONTWRITEBYTECODE=1" in worker
    assert "MEETING_COPILOT_FUNASR_MODEL_DIR" in backend
    assert "MEETING_COPILOT_RUNTIME_MANIFEST" in backend
    assert "--timeout-graceful-shutdown 8" in backend
    assert 'exec "$ROOT/runtime/backend-python/bin/python3.13"' in backend
    assert 'exec "$ROOT/runtime/funasr-python/bin/python3.11"' in worker
    assert 'export PYTHONHOME="$ROOT/runtime/funasr-python"' in worker


def test_file_asr_capability_reports_optional_models_without_copying_them(tmp_path):
    tool = load_tool_module()
    manifest = tool.load_runtime_manifest(REPO_ROOT)
    bundle = tmp_path / "MeetingCopilotRuntime.bundle"
    for relative in (
        manifest["runtimes"]["funasr"]["executable"],
        manifest["workers"]["file_asr"],
    ):
        path = bundle / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture", encoding="utf-8")

    capability = tool.inspect_file_asr_capability(bundle, manifest)

    assert capability["status"] == "file_asr_models_not_installed"
    assert capability["available"] is False
    assert capability["missing_components"] == [
        "offline_model",
        "vad_model",
        "punc_model",
    ]
    assert capability["model_download_performed"] is False


def test_file_asr_model_pack_requires_all_three_explicit_sources(tmp_path):
    tool = load_tool_module()
    repo = tmp_path / "repo"
    (repo / "artifacts/tmp").mkdir(parents=True)
    (repo / "code/desktop_tauri").mkdir(parents=True)
    (repo / "code/desktop_tauri/runtime-bundle-manifest.json").write_text(
        (REPO_ROOT / "code/desktop_tauri/runtime-bundle-manifest.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="requires offline, vad, and punc"):
        tool.build_and_probe(
            repo_root=repo,
            output_root=repo / "artifacts/tmp/runtime",
            run_id="partial-file-model-pack",
            model_dir=repo / "missing-realtime-model",
            file_asr_model_dirs={"offline": tmp_path / "offline"},
        )


def test_diarization_model_pack_requires_explicit_pair_and_manifest(tmp_path):
    tool = load_tool_module()
    repo = tmp_path / "repo"
    (repo / "artifacts/tmp").mkdir(parents=True)
    (repo / "code/desktop_tauri").mkdir(parents=True)
    (repo / "code/desktop_tauri/runtime-bundle-manifest.json").write_text(
        (REPO_ROOT / "code/desktop_tauri/runtime-bundle-manifest.json").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="must be provided together"):
        tool.build_and_probe(
            repo_root=repo,
            output_root=repo / "artifacts/tmp/runtime",
            run_id="partial-diarization-model-pack",
            model_dir=repo / "missing-realtime-model",
            diarization_vad_model_dir=tmp_path / "vad",
        )

    assert not (repo / "artifacts/tmp/runtime/partial-diarization-model-pack").exists()


def test_runtime_spike_cli_forwards_diarization_options_without_implicit_sources(
    tmp_path, monkeypatch, capsys
):
    tool = load_tool_module()
    captured: dict = {}

    def fake_build_and_probe(**kwargs):
        captured.update(kwargs)
        return {
            "decision": {
                "counts_as_local_relocation_evidence": True,
                "counts_as_public_release_package": False,
            }
        }

    monkeypatch.setattr(tool, "build_and_probe", fake_build_and_probe)
    vad = tmp_path / "vad"
    camplus = tmp_path / "camplus"
    policy = tmp_path / "diarization.manifest.json"

    result = tool.main([
        "--repo-root",
        str(tmp_path / "repo"),
        "--output-root",
        str(tmp_path / "repo/artifacts/tmp/runtime"),
        "--run-id",
        "diarization-cli",
        "--model-dir",
        str(tmp_path / "realtime"),
        "--diarization-vad-model-dir",
        str(vad),
        "--diarization-camplus-model-dir",
        str(camplus),
        "--diarization-model-pack-manifest",
        str(policy),
    ])

    assert result == 0
    assert captured["diarization_vad_model_dir"] == vad
    assert captured["diarization_camplus_model_dir"] == camplus
    assert captured["diarization_model_pack_manifest"] == policy
    assert json.loads(capsys.readouterr().out)["decision"][
        "counts_as_public_release_package"
    ] is False


def test_release_decision_never_treats_local_relocation_as_public_release():
    tool = load_tool_module()

    decision = tool.spike_decision(
        backend_probe={"status": "passed"},
        asr_probe={"status": "passed"},
        external_link_count=0,
    )

    assert decision["status"] == "go_local_relocatable_runtime_spike_not_public_release"
    assert decision["counts_as_local_relocation_evidence"] is True
    assert decision["counts_as_clean_mac_evidence"] is False
    assert decision["counts_as_public_release_package"] is False
