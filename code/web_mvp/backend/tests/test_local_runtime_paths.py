import json
from pathlib import Path

from meeting_copilot_web_mvp.local_runtime_paths import (
    packaged_funasr_environment,
    read_runtime_manifest,
    resolve_manifest_component,
    venv_python_path,
)


def test_windows_venv_python_uses_scripts_executable() -> None:
    root = Path("runtime") / ".venv-funasr"

    assert venv_python_path(root, platform_name="nt") == root / "Scripts" / "python.exe"


def test_posix_venv_python_uses_bin_executable() -> None:
    root = Path("runtime") / ".venv-funasr"

    assert venv_python_path(root, platform_name="posix") == root / "bin" / "python"


def _write_runtime_manifest(tmp_path: Path, *, worker_path: str = "app/worker.py") -> Path:
    manifest_path = tmp_path / "runtime-bundle-manifest.json"
    payload = {
        "schema_version": "meeting_copilot.runtime_bundle.v1",
        "packaged_python": {"funasr": {"path": "runtime/funasr-python"}},
        "runtimes": {
            "funasr": {
                "root": "runtime/funasr-venv",
                "site_packages": "runtime/funasr-venv/Lib/site-packages",
            }
        },
        "file_asr": {"runtime": {"root": "runtime/funasr-venv"}},
        "realtime_runtime": {"root": "runtime/funasr-onnx"},
        "workers": {"realtime": worker_path},
        "component_inventory": {
            "schema_version": "meeting_copilot.runtime_component_inventory.v1",
            "status": "sealed",
            "components": {
                "shared_asr.python_runtime": {
                    "kind": "directory",
                    "path": "runtime/funasr-python",
                },
                "shared_asr.runtime": {
                    "kind": "directory",
                    "path": "runtime/funasr-venv",
                },
                "realtime_asr.onnx_runtime": {
                    "kind": "directory",
                    "path": "runtime/funasr-onnx",
                },
                "realtime_asr.worker": {
                    "kind": "file",
                    "path": worker_path,
                },
            },
        },
    }
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    return manifest_path


def test_sealed_manifest_component_and_funasr_environment_stay_inside_bundle(tmp_path):
    manifest_path = _write_runtime_manifest(tmp_path)
    for relative in (
        "runtime/funasr-python/python.exe",
        "runtime/funasr-venv/Lib/site-packages/funasr/__init__.py",
        "runtime/funasr-onnx/funasr_onnx/__init__.py",
        "app/worker.py",
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture", encoding="utf-8")
    manifest = read_runtime_manifest(
        {"MEETING_COPILOT_RUNTIME_MANIFEST": str(manifest_path)}
    )

    worker = resolve_manifest_component(
        manifest,
        component_name="realtime_asr.worker",
        expected_kind="file",
        mirrored_fields=(("workers", "realtime"),),
    )
    environment = packaged_funasr_environment(
        manifest,
        worker_path=worker.path,
        include_realtime_runtime=True,
    )

    assert worker.path == tmp_path / "app" / "worker.py"
    assert worker.errors == ()
    assert environment.errors == ()
    assert environment.values["PYTHONHOME"] == str(tmp_path / "runtime" / "funasr-python")
    assert str(tmp_path / "runtime" / "funasr-onnx") in environment.values["PYTHONPATH"]
    assert str(tmp_path / "runtime" / "funasr-venv" / "Lib" / "site-packages") in environment.values["PYTHONPATH"]


def test_sealed_manifest_rejects_parent_traversal(tmp_path):
    manifest_path = _write_runtime_manifest(tmp_path, worker_path="../outside.py")
    manifest = read_runtime_manifest(
        {"MEETING_COPILOT_RUNTIME_MANIFEST": str(manifest_path)}
    )

    worker = resolve_manifest_component(
        manifest,
        component_name="realtime_asr.worker",
        expected_kind="file",
        mirrored_fields=(("workers", "realtime"),),
    )

    assert worker.path is None
    assert "unsafe_manifest_path" in worker.errors
