from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools/windows_bundled_runtime.py"


def load_tool_module():
    spec = importlib.util.spec_from_file_location("windows_bundled_runtime", TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write_onnx_fixture(tmp_path: Path):
    tool = load_tool_module()
    runtime = tmp_path / "runtime"
    model = tmp_path / "model"
    runtime.mkdir()
    model.mkdir()
    runtime_files = {
        "funasr_onnx/__init__.py": b"onnx adapter",
        "onnxruntime/__init__.py": b"onnx runtime",
        "numpy/__init__.py": b"numpy",
        "yaml/__init__.py": b"yaml",
    }
    model_files = {
        "model.onnx": b"encoder",
        "decoder.onnx": b"decoder",
        "config.yaml": b"config",
        "am.mvn": b"mvn",
        "tokens.json": b"tokens",
    }
    for relative, payload in runtime_files.items():
        destination = runtime / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
    for relative, payload in model_files.items():
        (model / relative).write_bytes(payload)
    readme = tmp_path / "README.md"
    readme.write_text("---\nlicense: Apache License 2.0\n---\n", encoding="utf-8")
    runtime_inventory = tool.package_runtime._directory_inventory(
        runtime,
        allowed_root=tmp_path,
    )
    model_inventory = tool.package_runtime._directory_inventory(
        model,
        allowed_root=tmp_path,
    )
    manifest = {
        "schema_version": tool.ONNX_PACK_SCHEMA,
        "pack_id": "fixture",
        "version": "fixture-v1",
        "engine": "onnx",
        "platform": "windows",
        "architecture": "x86_64",
        "redistribution": {
            "status": "public_redistribution_unresolved",
            "license_evidence_sha256": _sha256(readme.read_bytes()),
            "public_redistribution_approved": False,
        },
        "runtime": {
            "source_inventory": runtime_inventory,
            "required_imports": ["funasr_onnx", "onnxruntime", "numpy", "yaml"],
        },
        "model": {
            "model_id": "fixture/paraformer-online",
            "root": "models/funasr-online-onnx",
            "files": {
                relative: _sha256(payload)
                for relative, payload in model_files.items()
            },
            "inventory": model_inventory,
        },
    }
    manifest_path = tmp_path / "onnx-pack.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return tool, runtime, model, readme, manifest_path


def test_controlled_onnx_pack_validates_runtime_model_and_license(tmp_path):
    tool, runtime, model, readme, manifest_path = _write_onnx_fixture(tmp_path)

    validated = tool.validate_onnx_pack(
        runtime_root=runtime,
        model_root=model,
        model_readme=readme,
        manifest_path=manifest_path,
    )

    assert validated["manifest"]["engine"] == "onnx"
    assert validated["runtime_inventory"]["file_count"] == 4
    assert validated["model_inventory"]["file_count"] == 5


def test_controlled_onnx_pack_fails_closed_after_model_tampering(tmp_path):
    tool, runtime, model, readme, manifest_path = _write_onnx_fixture(tmp_path)
    (model / "decoder.onnx").write_bytes(b"tampered")

    with pytest.raises(ValueError, match="model file hash mismatch"):
        tool.validate_onnx_pack(
            runtime_root=runtime,
            model_root=model,
            model_readme=readme,
            manifest_path=manifest_path,
        )


def test_windows_manifest_routes_backend_and_both_asr_passes_to_packaged_assets(tmp_path):
    tool, runtime, model, readme, manifest_path = _write_onnx_fixture(tmp_path)
    validated = tool.validate_onnx_pack(
        runtime_root=runtime,
        model_root=model,
        model_readme=readme,
        manifest_path=manifest_path,
    )
    base = json.loads(tool.BASE_MANIFEST.read_text(encoding="utf-8"))

    manifest = tool.windows_manifest(base, validated)

    assert manifest["platform"] == "windows"
    assert manifest["architectures"] == ["x86_64"]
    assert manifest["launchers"]["backend"].endswith(".cmd")
    assert manifest["runtimes"]["backend"]["executable"].endswith("python.exe")
    assert manifest["runtimes"]["funasr"]["python_version"] == "3.12"
    assert manifest["realtime_model"]["engine"] == "onnx"
    assert set(manifest["realtime_model"]["required_files"]) == {
        "am.mvn",
        "config.yaml",
        "decoder.onnx",
        "model.onnx",
        "tokens.json",
    }
    assert "funasr_offline_refiner_worker.py" in manifest["workers"]["offline_refiner"]
    assert not any("native-mic" in path for path in manifest["required_files"])
    assert not any(path.endswith("model.pt") for path in manifest["required_files"])
    assert manifest["app_identity"] == tool.package_runtime.EXPECTED_WINDOWS_APP_IDENTITY
    assert tool.package_runtime._validate_runtime_manifest_contract(manifest)


def test_windows_launchers_bind_onnx_preview_and_isolated_offline_refiner(tmp_path):
    tool, runtime, model, readme, manifest_path = _write_onnx_fixture(tmp_path)
    validated = tool.validate_onnx_pack(
        runtime_root=runtime,
        model_root=model,
        model_readme=readme,
        manifest_path=manifest_path,
    )
    manifest = tool.windows_manifest(
        json.loads(tool.BASE_MANIFEST.read_text(encoding="utf-8")),
        validated,
    )
    bundle = tmp_path / "bundle"

    tool.write_windows_launchers(bundle, manifest)

    backend = (bundle / manifest["launchers"]["backend"]).read_text(encoding="ascii")
    worker = (bundle / manifest["launchers"]["funasr"]).read_text(encoding="ascii")
    assert "MEETING_COPILOT_FUNASR_ENGINE=onnx" in backend
    assert "MEETING_COPILOT_REALTIME_REFINER_PYTHONPATH" in backend
    assert "MEETING_COPILOT_FUNASR_SITE_PACKAGES" in backend
    assert "MEETING_COPILOT_REALTIME_REFINER_PREWARM_TIMEOUT_SECONDS=120" in backend
    assert "funasr-offline" not in backend
    assert "funasr_offline_refiner_worker.py" in backend
    assert "--engine onnx" in worker
    assert "MEETING_COPILOT_FUNASR_SITE_PACKAGES" in worker
    assert "MEETING_COPILOT_REALTIME_REFINER_PREWARM_TIMEOUT_SECONDS=120" in worker
    assert "API_KEY" not in backend
    assert "Authorization" not in backend
    assert b"\r\n" in (bundle / manifest["launchers"]["backend"]).read_bytes()


def test_windows_runtime_output_is_restricted_to_ignored_artifacts(tmp_path):
    tool = load_tool_module()

    with pytest.raises(ValueError, match="below artifacts/tmp"):
        tool._resolve_output(tmp_path / "bundle")

    approved = tool._resolve_output(
        REPO_ROOT / "artifacts/tmp/windows_runtime/test.bundle"
    )
    assert approved == (
        REPO_ROOT / "artifacts/tmp/windows_runtime/test.bundle"
    ).resolve()


def test_copy_tree_supports_packaged_dependency_paths_beyond_260_characters(tmp_path):
    tool = load_tool_module()
    source = tmp_path / "source"
    source.mkdir()
    relative = Path("site-packages") / ("deep-package-" + "x" * 90) / ("y" * 90 + ".py")
    source_file = tool._extended_windows_path(source / relative)
    source_file.parent.mkdir(parents=True)
    source_file.write_bytes(b"runtime dependency")
    destination = tmp_path / ("destination-" + "z" * 60)

    tool._copy_tree(source, destination)

    copied = tool._extended_windows_path(destination / relative)
    assert len(str((destination / relative).resolve())) > 260
    assert copied.read_bytes() == b"runtime dependency"


def test_packaged_sitecustomize_processes_controlled_pth_files(tmp_path):
    site_packages = tmp_path / "Lib/site-packages"
    extra = tmp_path / "extra"
    site_packages.mkdir(parents=True)
    extra.mkdir()
    (site_packages / "fixture.pth").write_text(str(extra) + "\n", encoding="utf-8")
    (extra / "packaged_pth_fixture.py").write_text(
        "VALUE = 'ready'\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT / "code/asr_runtime/scripts")
    env["MEETING_COPILOT_FUNASR_SITE_PACKAGES"] = str(site_packages)

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import packaged_pth_fixture; print(packaged_pth_fixture.VALUE)",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "ready"
