from __future__ import annotations

import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools/windows_thin_runtime_bundle.py"


def load_tool_module():
    spec = importlib.util.spec_from_file_location("windows_thin_runtime_bundle", TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _component(path: str, size: int) -> dict[str, object]:
    return {
        "path": path,
        "kind": "directory",
        "version": "fixture-v1",
        "size_bytes": size,
        "sha256": "a" * 64,
        "file_count": 1,
        "symlink_count": 0,
    }


def test_capability_catalog_splits_shared_realtime_and_file_asr_payloads():
    tool = load_tool_module()
    components = {
        "shared_asr.python_runtime": _component("runtime/python", 100),
        "shared_asr.sitecustomize": {
            **_component("app/sitecustomize.py", 1),
            "kind": "file",
        },
        "realtime_asr.onnx_runtime": _component("runtime/onnx", 200),
        "realtime_asr.model": _component("models/realtime", 300),
        "shared_asr.runtime": _component("runtime/funasr", 400),
        "file_asr.model.offline": _component("models/offline", 500),
        "file_asr.model.vad": _component("models/vad", 600),
        "file_asr.model.punc": _component("models/punc", 700),
    }
    manifest = {
        "component_inventory": {"components": components},
        "realtime_model": {"version": "realtime-v1"},
        "file_asr": {"package": {"version": "file-v1"}},
    }

    catalog = tool.build_capability_pack_catalog(
        manifest, base_url="https://downloads.example.cn/meeting-copilot/"
    )

    packs = catalog["packs"]
    assert set(packs) == {
        "asr-runtime-windows-x86_64",
        "realtime-asr-zh-cn",
        "file-asr-zh-cn",
    }
    assert packs["realtime-asr-zh-cn"]["depends_on"] == [
        "asr-runtime-windows-x86_64"
    ]
    assert packs["file-asr-zh-cn"]["unpacked_size_bytes"] == 2200
    assert packs["realtime-asr-zh-cn"]["urls"][0].startswith(
        "https://downloads.example.cn/meeting-copilot/packs/"
    )
    assert packs["realtime-asr-zh-cn"]["archive_sha256"] is None
    assert packs["realtime-asr-zh-cn"]["auto_install_allowed"] is False


def test_base_payload_copy_excludes_models_and_asr_runtimes(tmp_path):
    tool = load_tool_module()
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    for relative in (
        "runtime/backend-python/python.exe",
        "runtime/backend-venv/Lib/site-packages/base.py",
        "runtime/funasr-python/python.exe",
        "runtime/funasr-venv/Lib/site-packages/funasr.py",
        "runtime/funasr-onnx/onnxruntime/__init__.py",
        "models/funasr-online-onnx/model.onnx",
        "models/funasr-file/offline/model.pt",
        "bin/meeting-copilot-asr-worker.cmd",
        "licenses/models/fixture/LICENSE.txt",
    ):
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")

    tool._copy_base_payload(source, destination)

    assert (destination / "runtime/backend-python/python.exe").is_file()
    assert (destination / "runtime/backend-venv/Lib/site-packages/base.py").is_file()
    assert (destination / "bin/meeting-copilot-asr-worker.cmd").is_file()
    assert (destination / "licenses/models/fixture/LICENSE.txt").is_file()
    assert not (destination / "runtime/funasr-python").exists()
    assert not (destination / "runtime/funasr-venv").exists()
    assert not (destination / "runtime/funasr-onnx").exists()
    assert not (destination / "models").exists()
