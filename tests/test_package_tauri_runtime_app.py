import importlib.util
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "package_tauri_runtime_app.py"
MANIFEST_PATH = REPO_ROOT / "code/desktop_tauri/runtime-bundle-manifest.json"


def load_tool_module():
    spec = importlib.util.spec_from_file_location(
        "package_tauri_runtime_app", TOOL_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _write_required_files(bundle: Path, manifest: dict) -> None:
    for relative in manifest["required_files"]:
        if relative == "runtime-bundle-manifest.json":
            continue
        path = bundle / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _tree_sha256(files: dict[str, bytes]) -> tuple[int, str]:
    entries = [
        {
            "path": relative,
            "size_bytes": len(payload),
            "sha256": _sha256(payload),
        }
        for relative, payload in sorted(files.items())
    ]
    encoded = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode("ascii")
    return sum(item["size_bytes"] for item in entries), _sha256(encoded)


def _sealed_tree_sha256(files: dict[str, bytes]) -> tuple[int, str]:
    entries = [
        {
            "path": relative,
            "kind": "file",
            "size_bytes": len(payload),
            "sha256": _sha256(payload),
        }
        for relative, payload in sorted(files.items())
    ]
    encoded = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode("ascii")
    return sum(item["size_bytes"] for item in entries), _sha256(encoded)


def _write_controlled_model_pack(
    tmp_path: Path, *, tamper: bool = False
) -> tuple[Path, Path]:
    pack_root = tmp_path / "controlled-model-pack"
    policy_root = tmp_path / "policy"
    license_path = policy_root / "licenses/Apache-2.0.txt"
    license_path.parent.mkdir(parents=True)
    license_path.write_text("Apache License 2.0 fixture\n", encoding="utf-8")
    models: dict[str, dict] = {}
    model_ids = {
        "offline": "iic/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
        "vad": "iic/speech_fsmn_vad_zh-cn-16k-common-pytorch",
        "punc": "iic/punc_ct-transformer_cn-en-common-vocab471067-large",
    }
    for name, model_id in model_ids.items():
        source = pack_root / name
        source.mkdir(parents=True)
        files = {
            "model.pt": f"{name}-model".encode(),
            "config.yaml": f"name: {name}\n".encode(),
        }
        for relative, payload in files.items():
            (source / relative).write_bytes(payload)
        readme = b"---\nlicense: Apache License 2.0\n---\n"
        (source / "README.md").write_bytes(readme)
        (source / "must-not-be-copied.bin").write_bytes(b"extra")
        size_bytes, tree_sha256 = _tree_sha256(files)
        models[name] = {
            "model_id": model_id,
            "version": "fixture-v1",
            "source_url": f"https://www.modelscope.cn/models/{model_id}",
            "source_directory": name,
            "license_evidence": {
                "path": "README.md",
                "sha256": _sha256(readme),
                "required_text": "license: Apache License 2.0",
                "scope": "model_readme_metadata_only",
            },
            "files": {
                relative: _sha256(payload) for relative, payload in files.items()
            },
            "size_bytes": size_bytes,
            "sha256": tree_sha256,
        }
    if tamper:
        (pack_root / "offline/model.pt").write_bytes(b"tampered")
    policy = {
        "schema_version": "meeting_copilot.controlled_model_pack.v1",
        "pack_id": "file-asr-zh-cn",
        "version": "fixture-pack-v1",
        "internal_controlled_smoke": {
            "status": "internal_controlled_smoke",
            "counts_as_public_release": False,
        },
        "redistribution": {
            "status": "public_redistribution_unresolved",
            "license_id": "Apache-2.0",
            "license_text": "licenses/Apache-2.0.txt",
            "license_sha256": _sha256(license_path.read_bytes()),
            "notice_destination": "licenses/models/fixture-pack-v1",
            "public_redistribution_approved": False,
        },
        "models": models,
    }
    policy_path = policy_root / "model-pack.manifest.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    return pack_root, policy_path


def _write_controlled_diarization_model_pack(
    tmp_path: Path,
    *,
    tamper_component: str | None = None,
    add_symlink: bool = False,
) -> tuple[Path, Path, Path]:
    source_root = tmp_path / "diarization-sources"
    policy_root = tmp_path / "diarization-policy"
    policy_root.mkdir(parents=True)
    definitions = {
        "vad": {
            "model_id": "iic/speech_fsmn_vad_zh-cn-16k-common-pytorch",
            "version": "vad-v1.0.0",
            "files": {
                "model.pt": b"vad-model-weights",
                "config.yaml": b"model: fixture-vad\n",
            },
        },
        "camplus": {
            "model_id": "damo/speech_campplus_sv_zh-cn_16k-common",
            "version": "camplus-v2.0.2",
            "files": {
                "campplus_cn_common.bin": b"camplus-model-weights",
                "config.yaml": b"model: fixture-camplus\n",
            },
        },
    }
    models: dict[str, dict] = {}
    inventories: dict[str, dict] = {}
    for name, definition in definitions.items():
        source = source_root / name
        source.mkdir(parents=True)
        files = definition["files"]
        for relative, payload in files.items():
            (source / relative).write_bytes(payload)
        (source / "must-not-be-copied.bin").write_bytes(b"extra")
        size_bytes, inventory_sha256 = _sealed_tree_sha256(files)
        inventories[name] = {
            "size_bytes": size_bytes,
            "sha256": inventory_sha256,
        }
        version = definition["version"]
        models[name] = {
            "model_id": definition["model_id"],
            "version": version,
            "provenance": {
                "provider": "modelscope",
                "source_url": (
                    f"https://modelscope.cn/models/{definition['model_id']}"
                ),
                "immutable_revision": version,
                "license_id": "Apache-2.0",
                "license_evidence_scope": (
                    "upstream_model_metadata_observed_not_public_redistribution_approval"
                ),
                "public_redistribution_approved": False,
            },
            "files": {
                relative: _sha256(payload) for relative, payload in files.items()
            },
            "size_bytes": size_bytes,
            "sha256": inventory_sha256,
        }
    aggregate_entries = [
        {
            "name": name,
            "size_bytes": inventory["size_bytes"],
            "sha256": inventory["sha256"],
        }
        for name, inventory in sorted(inventories.items())
    ]
    aggregate_encoded = json.dumps(
        aggregate_entries, sort_keys=True, separators=(",", ":")
    ).encode("ascii")
    policy = {
        "schema_version": "meeting_copilot.controlled_diarization_model_pack.v1",
        "pack_id": "diarization-zh-cn",
        "version": "diarization-fixture-v1",
        "size_bytes": sum(item["size_bytes"] for item in aggregate_entries),
        "sha256": _sha256(aggregate_encoded),
        "offline_boundary": {
            "requires_network": False,
            "runtime_downloads_allowed": False,
            "remote_asr_used": False,
        },
        "verification": {
            "status": "verified_for_internal_packaging",
            "counts_as_public_release": False,
        },
        "redistribution": {
            "status": "public_redistribution_unresolved",
            "notice_destination": "licenses/models/diarization-fixture-v1",
            "public_redistribution_approved": False,
        },
        "models": models,
    }
    policy_path = policy_root / "model-pack.manifest.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    if tamper_component is not None:
        tamper_file = (
            "model.pt" if tamper_component == "vad" else "campplus_cn_common.bin"
        )
        (source_root / tamper_component / tamper_file).write_bytes(b"tampered")
    if add_symlink:
        (source_root / "vad/uncontrolled-link").symlink_to("model.pt")
    return source_root / "vad", source_root / "camplus", policy_path


def _write_base_runtime_bundle(tmp_path: Path) -> tuple[Path, dict]:
    bundle = tmp_path / "source/MeetingCopilotRuntime.bundle"
    bundle.mkdir(parents=True)
    manifest = _manifest()
    (bundle / "runtime-bundle-manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    _write_required_files(bundle, manifest)
    converter = bundle / manifest["file_asr"]["converter"]["path"]
    converter.parent.mkdir(parents=True, exist_ok=True)
    converter.write_bytes(b"controlled-ffmpeg")
    converter.chmod(0o755)
    license_path = bundle / manifest["file_asr"]["converter"]["license_path"]
    license_path.parent.mkdir(parents=True, exist_ok=True)
    license_path.write_text("BSD fixture\n", encoding="utf-8")
    return bundle, manifest


def test_overlay_maps_runtime_to_stable_app_resources_path(tmp_path):
    tool = load_tool_module()
    bundle = tmp_path / "MeetingCopilotRuntime.bundle"
    overlay_path = tmp_path / "overlay.json"

    overlay = tool.build_overlay(bundle, overlay_path)

    assert (
        overlay["bundle"]["resources"][str(bundle.resolve())]
        == "MeetingCopilotRuntime.bundle"
    )
    assert json.loads(overlay_path.read_text(encoding="utf-8")) == overlay


def test_validate_runtime_bundle_fails_closed_for_missing_inventory(tmp_path):
    tool = load_tool_module()

    with pytest.raises(ValueError, match="runtime bundle missing required files"):
        tool.validate_runtime_bundle(tmp_path / "missing")


def test_prepare_runtime_bundle_seals_embedded_versions_and_component_hashes(tmp_path):
    tool = load_tool_module()
    bundle, manifest = _write_base_runtime_bundle(tmp_path)
    replacements = {
        manifest["runtimes"]["backend"]["executable"]: "runtime/backend/bin/python9.8",
        manifest["runtimes"]["funasr"]["executable"]: "runtime/funasr/bin/python7.6",
    }
    manifest["runtimes"]["backend"].update(
        {
            "python_version": "9.8",
            "executable": replacements[manifest["runtimes"]["backend"]["executable"]],
        }
    )
    manifest["runtimes"]["funasr"].update(
        {
            "python_version": "7.6",
            "executable": replacements[manifest["runtimes"]["funasr"]["executable"]],
        }
    )
    manifest["required_files"] = [
        replacements.get(value, value) for value in manifest["required_files"]
    ]
    (bundle / "runtime-bundle-manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    _write_required_files(bundle, manifest)

    prepared = tool.prepare_runtime_bundle_for_packaging(
        source_bundle=bundle,
        destination_bundle=tmp_path / "prepared/MeetingCopilotRuntime.bundle",
    )
    loaded = tool.validate_runtime_bundle(Path(prepared["bundle_path"]))

    assert loaded["runtimes"]["funasr"]["component_version"] == "funasr-1.3.10"
    assert loaded["component_inventory"]["status"] == "sealed"
    assert (
        loaded["file_asr"]["runtime"]["executable"]
        == "bin/meeting-copilot-file-asr-python"
    )
    assert (
        loaded["runtimes"]["funasr"]["venv_executable"]
        == "bin/meeting-copilot-file-asr-python"
    )
    launcher = Path(prepared["bundle_path"]) / "bin/meeting-copilot-file-asr-python"
    assert launcher.stat().st_mode & 0o111
    launcher_source = launcher.read_text(encoding="utf-8")
    assert str(Path.home()) not in launcher_source
    assert "export PYTHONDONTWRITEBYTECODE=1" in launcher_source
    assert loaded["component_inventory"]["components"]["realtime_asr.worker"]["sha256"]
    assert loaded["realtime_model"]["sha256"]
    assert loaded["file_asr"]["converter"]["sha256"] == _sha256(b"controlled-ffmpeg")


def test_refresh_runtime_manifest_after_nested_signing_reseals_mutated_file(tmp_path):
    tool = load_tool_module()
    bundle, _manifest_payload = _write_base_runtime_bundle(tmp_path)
    prepared = tool.prepare_runtime_bundle_for_packaging(
        source_bundle=bundle,
        destination_bundle=tmp_path / "prepared/MeetingCopilotRuntime.bundle",
    )
    prepared_bundle = Path(prepared["bundle_path"])
    converter = prepared_bundle / (
        "runtime/backend-venv/lib/python3.13/site-packages/"
        "imageio_ffmpeg/binaries/ffmpeg-macos-aarch64-v7.1"
    )
    converter.write_bytes(b"materialized-and-signed-converter")

    refreshed = tool.refresh_runtime_manifest_after_nested_signing(prepared_bundle)

    record = refreshed["component_inventory"]["components"]["file_asr.converter"]
    assert record["size_bytes"] == converter.stat().st_size
    assert record["sha256"] == tool.sha256_file(converter)
    assert refreshed["file_asr"]["converter"]["size_bytes"] == converter.stat().st_size


def test_preflight_reports_optional_file_models_as_missing_without_failing_core_bundle(
    tmp_path,
):
    tool = load_tool_module()
    bundle, _manifest_payload = _write_base_runtime_bundle(tmp_path)
    prepared = tool.prepare_runtime_bundle_for_packaging(
        source_bundle=bundle,
        destination_bundle=tmp_path / "prepared/MeetingCopilotRuntime.bundle",
    )
    prepared_bundle = Path(prepared["bundle_path"])

    loaded = tool.validate_runtime_bundle(prepared_bundle)
    capability = tool.inspect_file_asr_capability(prepared_bundle, loaded)

    assert capability["status"] == "file_asr_models_not_installed"
    assert capability["missing_components"] == [
        "offline_model",
        "vad_model",
        "punc_model",
    ]
    assert capability["available"] is False
    assert capability["user_message"] == "文件导入组件未安装"
    assert capability["network_offline"] is None
    assert capability["requires_network"] is False


def test_optional_diarization_models_are_absent_and_fail_open(tmp_path):
    tool = load_tool_module()
    bundle, _manifest_payload = _write_base_runtime_bundle(tmp_path)

    prepared = tool.prepare_runtime_bundle_for_packaging(
        source_bundle=bundle,
        destination_bundle=tmp_path / "prepared/MeetingCopilotRuntime.bundle",
    )

    capability = prepared["diarization_capability"]
    assert capability["status"] == "absent_optional_fail_open"
    assert capability["available"] is False
    assert capability["fail_open"] is True
    assert capability["invalid_fail_closed"] is False
    assert capability["speaker_attribution_fallback"] == "unknown"
    assert capability["recording_and_asr_continue"] is True
    assert capability["requires_network"] is False
    assert capability["runtime_downloads_allowed"] is False
    assert prepared["controlled_diarization_model_pack"] is None
    assert prepared["manifest"]["diarization"]["package"] == {
        "pack_id": "diarization-zh-cn",
        "install_status": "not_bundled",
        "verification_status": "absent_optional_fail_open",
        "version": None,
        "size_bytes": None,
        "sha256": None,
        "control_manifest_sha256": None,
        "redistribution_status": "public_redistribution_unresolved",
        "counts_as_public_release": False,
    }


@pytest.mark.parametrize(
    ("include_vad", "include_camplus", "include_manifest"),
    [
        (True, False, False),
        (False, True, False),
        (False, False, True),
        (True, True, False),
        (True, False, True),
        (False, True, True),
    ],
)
def test_diarization_model_sources_and_manifest_are_all_or_none(
    tmp_path,
    include_vad,
    include_camplus,
    include_manifest,
):
    tool = load_tool_module()
    bundle, _manifest_payload = _write_base_runtime_bundle(tmp_path)
    vad, camplus, policy = _write_controlled_diarization_model_pack(tmp_path)
    destination = tmp_path / "prepared/MeetingCopilotRuntime.bundle"

    with pytest.raises(ValueError, match="must be provided together"):
        tool.prepare_runtime_bundle_for_packaging(
            source_bundle=bundle,
            destination_bundle=destination,
            diarization_vad_model_dir=vad if include_vad else None,
            diarization_camplus_model_dir=camplus if include_camplus else None,
            diarization_model_pack_manifest=policy if include_manifest else None,
        )

    assert not destination.exists()


@pytest.mark.parametrize("component", ["vad", "camplus"])
def test_diarization_model_pack_hash_tamper_fails_before_copy(tmp_path, component):
    tool = load_tool_module()
    bundle, _manifest_payload = _write_base_runtime_bundle(tmp_path)
    vad, camplus, policy = _write_controlled_diarization_model_pack(
        tmp_path, tamper_component=component
    )
    destination = tmp_path / "prepared/MeetingCopilotRuntime.bundle"

    with pytest.raises(ValueError, match="file hash mismatch"):
        tool.prepare_runtime_bundle_for_packaging(
            source_bundle=bundle,
            destination_bundle=destination,
            diarization_vad_model_dir=vad,
            diarization_camplus_model_dir=camplus,
            diarization_model_pack_manifest=policy,
        )

    assert not destination.exists()


def test_diarization_model_pack_rejects_source_symlinks(tmp_path):
    tool = load_tool_module()
    bundle, _manifest_payload = _write_base_runtime_bundle(tmp_path)
    vad, camplus, policy = _write_controlled_diarization_model_pack(
        tmp_path, add_symlink=True
    )

    with pytest.raises(ValueError, match="source contains symlinks"):
        tool.prepare_runtime_bundle_for_packaging(
            source_bundle=bundle,
            destination_bundle=tmp_path / "prepared/MeetingCopilotRuntime.bundle",
            diarization_vad_model_dir=vad,
            diarization_camplus_model_dir=camplus,
            diarization_model_pack_manifest=policy,
        )


def test_inherited_external_symlink_parent_is_rejected_before_any_mutation(tmp_path):
    tool = load_tool_module()
    bundle, _manifest_payload = _write_base_runtime_bundle(tmp_path)
    vad, camplus, policy = _write_controlled_diarization_model_pack(tmp_path)
    external_models = tmp_path / "external-models"
    shutil.move(bundle / "models", external_models)
    (bundle / "models").symlink_to(external_models, target_is_directory=True)
    inherited_vad = external_models / "diarization-vad"
    inherited_vad.mkdir()
    external_marker = inherited_vad / "must-survive.txt"
    external_marker.write_text("external state\n", encoding="utf-8")
    destination = tmp_path / "prepared/MeetingCopilotRuntime.bundle"
    destination.mkdir(parents=True)
    destination_marker = destination / "must-survive.txt"
    destination_marker.write_text("existing destination\n", encoding="utf-8")

    with pytest.raises(
        ValueError, match="source runtime bundle contains external symlinks"
    ):
        tool.prepare_runtime_bundle_for_packaging(
            source_bundle=bundle,
            destination_bundle=destination,
            diarization_vad_model_dir=vad,
            diarization_camplus_model_dir=camplus,
            diarization_model_pack_manifest=policy,
        )

    assert external_marker.read_text(encoding="utf-8") == "external state\n"
    assert destination_marker.read_text(encoding="utf-8") == "existing destination\n"
    assert not (external_models / "diarization-camplus").exists()


def test_symlinked_destination_parent_is_rejected_before_cleanup(tmp_path):
    tool = load_tool_module()
    bundle, _manifest_payload = _write_base_runtime_bundle(tmp_path)
    external_destination_parent = tmp_path / "external-destination"
    destination = external_destination_parent / "MeetingCopilotRuntime.bundle"
    destination.mkdir(parents=True)
    marker = destination / "must-survive.txt"
    marker.write_text("existing destination\n", encoding="utf-8")
    linked_parent = tmp_path / "linked-destination"
    linked_parent.symlink_to(external_destination_parent, target_is_directory=True)

    with pytest.raises(
        ValueError, match="destination contains a symlinked path component"
    ):
        tool.prepare_runtime_bundle_for_packaging(
            source_bundle=bundle,
            destination_bundle=linked_parent / destination.name,
        )

    assert marker.read_text(encoding="utf-8") == "existing destination\n"


@pytest.mark.parametrize("manifest_key", ["vad_model", "model_pack"])
def test_physically_present_incomplete_diarization_root_is_invalid_fail_closed(
    tmp_path,
    manifest_key,
):
    tool = load_tool_module()
    bundle, manifest = _write_base_runtime_bundle(tmp_path)
    spec = manifest["diarization"][manifest_key]
    root = bundle / spec["root"]
    root.mkdir(parents=True)
    (root / spec["required_files"][0]).write_bytes(b"incomplete")

    capability = tool.inspect_diarization_capability(bundle, manifest)

    assert capability["status"] == "invalid_fail_closed"
    assert capability["invalid_fail_closed"] is True
    assert capability["fail_open"] is False
    assert capability["recording_and_asr_continue"] is False
    destination = tmp_path / "prepared/MeetingCopilotRuntime.bundle"
    with pytest.raises(ValueError, match=r"require explicit VAD/CAM\+\+ sources"):
        tool.prepare_runtime_bundle_for_packaging(
            source_bundle=bundle,
            destination_bundle=destination,
        )
    assert not destination.exists()


def test_controlled_diarization_model_pair_is_staged_verified_and_sealed(tmp_path):
    tool = load_tool_module()
    bundle, _manifest_payload = _write_base_runtime_bundle(tmp_path)
    vad, camplus, policy = _write_controlled_diarization_model_pack(tmp_path)

    prepared = tool.prepare_runtime_bundle_for_packaging(
        source_bundle=bundle,
        destination_bundle=tmp_path / "prepared/MeetingCopilotRuntime.bundle",
        diarization_vad_model_dir=vad,
        diarization_camplus_model_dir=camplus,
        diarization_model_pack_manifest=policy,
    )

    destination = Path(prepared["bundle_path"])
    sealed = tool.validate_runtime_bundle(destination)
    capability = prepared["diarization_capability"]
    assert capability["status"] == "bundled_verified"
    assert capability["available"] is True
    assert capability["fail_open"] is False
    assert capability["invalid_fail_closed"] is False
    assert capability["requires_network"] is False
    assert capability["runtime_downloads_allowed"] is False
    assert capability["counts_as_public_release"] is False
    assert sealed["diarization"]["package"]["install_status"] == "bundled"
    assert sealed["diarization"]["package"]["verification_status"] == "bundled_verified"
    assert sealed["diarization"]["package"]["counts_as_public_release"] is False
    assert (
        sealed["diarization"]["redistribution"]["status"]
        == "public_redistribution_unresolved"
    )
    assert (
        sealed["diarization"]["redistribution"]["public_redistribution_approved"]
        is False
    )
    assert sealed["diarization"]["redistribution"]["license_ids"] == ["Apache-2.0"]
    assert prepared["controlled_diarization_model_pack"]["offline_boundary"] == {
        "requires_network": False,
        "runtime_downloads_allowed": False,
        "remote_asr_used": False,
    }
    for name, manifest_key in {"vad": "vad_model", "camplus": "model_pack"}.items():
        model = sealed["diarization"][manifest_key]
        root = destination / model["root"]
        assert model["install_status"] == "bundled"
        assert model["sha256"] == model["source_inventory_sha256"]
        assert model["provenance"]["immutable_revision"] == model["version"]
        assert model["provenance"]["public_redistribution_approved"] is False
        assert not (root / "must-not-be-copied.bin").exists()
        assert (
            sealed["component_inventory"]["components"][f"diarization.model.{name}"][
                "sha256"
            ]
            == model["sha256"]
        )
    notices = destination / sealed["diarization"]["redistribution"]["notices_path"]
    control_manifest = notices / "model-pack.manifest.json"
    assert control_manifest.is_file()
    assert (
        _sha256(control_manifest.read_bytes())
        == sealed["diarization"]["package"]["control_manifest_sha256"]
    )


@pytest.mark.parametrize(
    "tamper_target",
    [
        "model_sha256",
        "source_inventory_sha256",
        "model_id",
        "model_version",
        "model_provenance",
        "package_sha256",
        "package_control_manifest_sha256",
        "redistribution_control_manifest_sha256",
        "component_provenance",
        "physical_control_manifest",
    ],
)
def test_sealed_diarization_metadata_tamper_fails_validation(
    tmp_path,
    tamper_target,
):
    tool = load_tool_module()
    bundle, _manifest_payload = _write_base_runtime_bundle(tmp_path)
    vad, camplus, policy = _write_controlled_diarization_model_pack(tmp_path)
    destination = tmp_path / "prepared/MeetingCopilotRuntime.bundle"
    tool.prepare_runtime_bundle_for_packaging(
        source_bundle=bundle,
        destination_bundle=destination,
        diarization_vad_model_dir=vad,
        diarization_camplus_model_dir=camplus,
        diarization_model_pack_manifest=policy,
    )
    manifest_path = destination / "runtime-bundle-manifest.json"
    sealed = json.loads(manifest_path.read_text(encoding="utf-8"))
    vad_model = sealed["diarization"]["vad_model"]
    if tamper_target == "model_sha256":
        vad_model["sha256"] = "0" * 64
    elif tamper_target == "source_inventory_sha256":
        vad_model["source_inventory_sha256"] = "0" * 64
    elif tamper_target == "model_id":
        vad_model["model_id"] = "iic/tampered-vad"
    elif tamper_target == "model_version":
        vad_model["version"] = "tampered-v9"
    elif tamper_target == "model_provenance":
        vad_model["provenance"]["provider"] = "tampered-provider"
    elif tamper_target == "package_sha256":
        sealed["diarization"]["package"]["sha256"] = "0" * 64
    elif tamper_target == "package_control_manifest_sha256":
        sealed["diarization"]["package"]["control_manifest_sha256"] = "0" * 64
    elif tamper_target == "redistribution_control_manifest_sha256":
        sealed["diarization"]["redistribution"]["control_manifest_sha256"] = "0" * 64
    elif tamper_target == "component_provenance":
        sealed["component_inventory"]["components"]["diarization.model.vad"][
            "provenance"
        ]["provider"] = "tampered-provider"
    else:
        control = (
            destination
            / sealed["component_inventory"]["components"][
                "diarization.control_manifest"
            ]["path"]
        )
        control.write_bytes(control.read_bytes() + b" ")
    if tamper_target != "physical_control_manifest":
        manifest_path.write_text(json.dumps(sealed), encoding="utf-8")

    with pytest.raises(ValueError, match="diarization|component hash"):
        tool.validate_runtime_bundle(destination)


def test_packaged_diarization_control_manifest_omits_unknown_sensitive_fields(tmp_path):
    tool = load_tool_module()
    bundle, _manifest_payload = _write_base_runtime_bundle(tmp_path)
    vad, camplus, policy = _write_controlled_diarization_model_pack(tmp_path)
    operator_manifest = json.loads(policy.read_text(encoding="utf-8"))
    secret = "operator-secret-token"
    operator_manifest["operator"] = {
        "local_source_path": str(vad),
        "access_token": secret,
    }
    operator_manifest["models"]["vad"]["local_source_path"] = str(vad)
    operator_manifest["models"]["vad"]["provenance"]["access_token"] = secret
    policy.write_text(json.dumps(operator_manifest), encoding="utf-8")

    prepared = tool.prepare_runtime_bundle_for_packaging(
        source_bundle=bundle,
        destination_bundle=tmp_path / "prepared/MeetingCopilotRuntime.bundle",
        diarization_vad_model_dir=vad,
        diarization_camplus_model_dir=camplus,
        diarization_model_pack_manifest=policy,
    )

    destination = Path(prepared["bundle_path"])
    sealed = prepared["manifest"]
    control_component = sealed["component_inventory"]["components"][
        "diarization.control_manifest"
    ]
    control_bytes = (destination / control_component["path"]).read_bytes()
    control = json.loads(control_bytes)
    assert secret.encode() not in control_bytes
    assert str(vad).encode() not in control_bytes
    assert "operator" not in control
    assert "local_source_path" not in control["models"]["vad"]
    assert "access_token" not in control["models"]["vad"]["provenance"]
    assert _sha256(control_bytes) == control_component["sha256"]
    assert (
        control_component["sha256"]
        == sealed["diarization"]["package"]["control_manifest_sha256"]
    )
    assert (
        control_component["sha256"]
        == sealed["diarization"]["redistribution"]["control_manifest_sha256"]
    )
    assert (
        prepared["controlled_diarization_model_pack"]["source_manifest_sha256"]
        != control_component["sha256"]
    )


def test_diarization_model_naming_mismatch_fails_before_copy(tmp_path):
    tool = load_tool_module()
    bundle, manifest = _write_base_runtime_bundle(tmp_path)
    manifest["diarization"]["vad_model"]["model_id"] = "iic/not-the-vad-model"
    (bundle / "runtime-bundle-manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    vad, camplus, policy = _write_controlled_diarization_model_pack(tmp_path)
    destination = tmp_path / "prepared/MeetingCopilotRuntime.bundle"

    with pytest.raises(ValueError, match="model id does not match runtime manifest"):
        tool.prepare_runtime_bundle_for_packaging(
            source_bundle=bundle,
            destination_bundle=destination,
            diarization_vad_model_dir=vad,
            diarization_camplus_model_dir=camplus,
            diarization_model_pack_manifest=policy,
        )

    assert not destination.exists()


def test_controlled_model_pack_is_allowlisted_staged_and_sealed(tmp_path):
    tool = load_tool_module()
    bundle, manifest = _write_base_runtime_bundle(tmp_path)
    pack_root, policy_path = _write_controlled_model_pack(tmp_path)
    destination = tmp_path / "prepared/MeetingCopilotRuntime.bundle"

    prepared = tool.prepare_runtime_bundle_for_packaging(
        source_bundle=bundle,
        destination_bundle=destination,
        model_pack_root=pack_root,
        model_pack_manifest=policy_path,
    )

    sealed = json.loads(
        (destination / "runtime-bundle-manifest.json").read_text(encoding="utf-8")
    )
    assert prepared["file_asr_capability"]["status"] == "ready"
    assert prepared["file_asr_capability"]["formats"]["wav"]["available"] is True
    assert prepared["file_asr_capability"]["formats"]["m4a"]["available"] is True
    assert prepared["file_asr_capability"]["formats"]["mp3"]["available"] is True
    assert sealed["file_asr"]["package"]["install_status"] == "bundled"
    assert (
        sealed["file_asr"]["package"]["redistribution_status"]
        == "public_redistribution_unresolved"
    )
    assert (
        sealed["file_asr"]["package"]["internal_controlled_smoke_status"]
        == "internal_controlled_smoke"
    )
    assert sealed["file_asr"]["package"]["counts_as_public_release"] is False
    assert sealed["file_asr"]["redistribution"]["license_id"] == "Apache-2.0"
    assert (
        sealed["file_asr"]["redistribution"]["public_redistribution_approved"] is False
    )
    assert len(sealed["file_asr"]["package"]["control_manifest_sha256"]) == 64
    assert prepared["package_decision"]["status"] == "internal_controlled_smoke"
    assert prepared["package_decision"]["counts_as_public_release"] is False
    for name in ("offline", "vad", "punc"):
        model = sealed["file_asr"]["models"][name]
        destination_model = destination / model["root"]
        assert (destination_model / "model.pt").is_file()
        assert (destination_model / "config.yaml").is_file()
        assert not (destination_model / "must-not-be-copied.bin").exists()
        assert len(model["sha256"]) == 64
    assert (
        destination / sealed["file_asr"]["redistribution"]["license_path"]
    ).is_file()
    assert (destination / sealed["file_asr"]["redistribution"]["notices_path"]).is_dir()
    tool.validate_runtime_bundle(destination)


def test_controlled_model_pack_fails_closed_on_hash_mismatch(tmp_path):
    tool = load_tool_module()
    bundle, _manifest_payload = _write_base_runtime_bundle(tmp_path)
    pack_root, policy_path = _write_controlled_model_pack(tmp_path, tamper=True)

    with pytest.raises(ValueError, match="controlled model pack file hash mismatch"):
        tool.prepare_runtime_bundle_for_packaging(
            source_bundle=bundle,
            destination_bundle=tmp_path / "prepared/MeetingCopilotRuntime.bundle",
            model_pack_root=pack_root,
            model_pack_manifest=policy_path,
        )


def test_file_asr_package_decision_fails_closed_when_models_are_complete_but_converter_license_is_missing(
    tmp_path,
):
    tool = load_tool_module()
    bundle, manifest = _write_base_runtime_bundle(tmp_path)
    (bundle / manifest["file_asr"]["converter"]["path"]).unlink()
    (bundle / manifest["file_asr"]["converter"]["license_path"]).unlink()
    pack_root, policy_path = _write_controlled_model_pack(tmp_path)

    prepared = tool.prepare_runtime_bundle_for_packaging(
        source_bundle=bundle,
        destination_bundle=tmp_path / "prepared/MeetingCopilotRuntime.bundle",
        model_pack_root=pack_root,
        model_pack_manifest=policy_path,
    )

    decision = prepared["package_decision"]
    assert decision["status"] == "internal_controlled_smoke_blocked"
    assert decision["counts_as_internal_controlled_smoke"] is False
    assert decision["counts_as_public_release"] is False
    assert (
        decision["public_redistribution_status"] == "public_redistribution_unresolved"
    )
    assert decision["blockers"] == ["converter_missing", "converter_license_missing"]
    assert all(
        format_decision["package_ready"] is False
        for format_decision in decision["formats"].values()
    )


def test_file_asr_package_fails_closed_when_converter_provenance_is_missing(tmp_path):
    tool = load_tool_module()
    bundle, manifest = _write_base_runtime_bundle(tmp_path)
    manifest["file_asr"]["converter"].pop("provenance")
    (bundle / "runtime-bundle-manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    pack_root, policy_path = _write_controlled_model_pack(tmp_path)

    with pytest.raises(ValueError, match="converter provenance is missing"):
        tool.prepare_runtime_bundle_for_packaging(
            source_bundle=bundle,
            destination_bundle=tmp_path / "prepared/MeetingCopilotRuntime.bundle",
            model_pack_root=pack_root,
            model_pack_manifest=policy_path,
        )


def test_prebundled_file_models_without_control_manifest_are_rejected(tmp_path):
    tool = load_tool_module()
    bundle, manifest = _write_base_runtime_bundle(tmp_path)
    for name, spec in manifest["file_asr"]["models"].items():
        root = bundle / spec["root"]
        root.mkdir(parents=True)
        for relative in spec["required_files"]:
            (root / relative).write_bytes(f"{name}:{relative}".encode())
    manifest["file_asr"]["package"].update(
        {
            "install_status": "bundled",
            "version": "uncontrolled",
            "size_bytes": 1,
            "sha256": "0" * 64,
        }
    )
    (bundle / "runtime-bundle-manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="validated controlled model pack manifest"):
        tool.prepare_runtime_bundle_for_packaging(
            source_bundle=bundle,
            destination_bundle=tmp_path / "prepared/MeetingCopilotRuntime.bundle",
        )


def test_prebundled_diarization_models_without_explicit_sources_are_rejected(tmp_path):
    tool = load_tool_module()
    bundle, manifest = _write_base_runtime_bundle(tmp_path)
    for manifest_key in ("vad_model", "model_pack"):
        spec = manifest["diarization"][manifest_key]
        root = bundle / spec["root"]
        root.mkdir(parents=True)
        for relative in spec["required_files"]:
            (root / relative).write_bytes(f"{manifest_key}:{relative}".encode())

    with pytest.raises(ValueError, match=r"require explicit VAD/CAM\+\+ sources"):
        tool.prepare_runtime_bundle_for_packaging(
            source_bundle=bundle,
            destination_bundle=tmp_path / "prepared/MeetingCopilotRuntime.bundle",
        )


def test_checked_in_control_manifest_matches_existing_controlled_model_pack():
    tool = load_tool_module()
    pack_root = REPO_ROOT / "artifacts/tmp/file-asr-model-pack/source-20260718"
    if not pack_root.is_dir():
        pytest.skip("controlled local model pack is not present on this machine")
    policy_path = (
        REPO_ROOT / "code/asr_runtime/model_packs/file-asr-zh-cn-20260718.manifest.json"
    )

    verified = tool.validate_controlled_model_pack(
        model_pack_root=pack_root,
        model_pack_manifest=policy_path,
    )

    assert verified["version"] == "file-asr-zh-cn-20260718-v1"
    assert verified["redistribution"]["status"] == "public_redistribution_unresolved"
    assert (
        verified["internal_controlled_smoke"]["status"] == "internal_controlled_smoke"
    )
    assert set(verified["models"]) == {"offline", "vad", "punc"}


def test_runtime_inventory_is_stable_when_tauri_materializes_internal_file_symlinks(
    tmp_path,
):
    tool = load_tool_module()
    bundle = tmp_path / "bundle"
    linked = bundle / "linked"
    materialized = bundle / "materialized"
    linked.mkdir(parents=True)
    materialized.mkdir(parents=True)
    (linked / "python-real").write_bytes(b"runtime-binary")
    (linked / "python").symlink_to("python-real")
    (materialized / "python-real").write_bytes(b"runtime-binary")
    (materialized / "python").write_bytes(b"runtime-binary")

    linked_inventory = tool._directory_inventory(linked, allowed_root=bundle)
    materialized_inventory = tool._directory_inventory(
        materialized, allowed_root=bundle
    )

    assert linked_inventory["sha256"] == materialized_inventory["sha256"]
    assert linked_inventory["size_bytes"] == materialized_inventory["size_bytes"]
    assert linked_inventory["symlink_count"] == 1
    assert materialized_inventory["symlink_count"] == 0


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda manifest: manifest["runtimes"]["funasr"].update(
                {"source_venv": "code/asr_runtime/.venv-funasr"}
            ),
            "source_venv",
        ),
        (
            lambda manifest: manifest["file_asr"]["models"]["offline"].update(
                {"root": ".cache/modelscope/offline"}
            ),
            "development/cache",
        ),
        (
            lambda manifest: manifest["file_asr"]["models"]["offline"].update(
                {"root": "/Users/example/Documents/repo/model"}
            ),
            "unsafe",
        ),
    ],
)
def test_preflight_rejects_development_repo_and_user_cache_references(
    tmp_path, mutate, message
):
    tool = load_tool_module()
    bundle = tmp_path / "MeetingCopilotRuntime.bundle"
    bundle.mkdir()
    manifest = _manifest()
    mutate(manifest)
    (bundle / "runtime-bundle-manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )

    with pytest.raises(ValueError, match=message):
        tool.load_runtime_bundle_manifest(bundle)


def test_fixed_identity_preflight_matches_tauri_and_manifest(tmp_path):
    tool = load_tool_module()
    repo = tmp_path / "repo"
    config_path = repo / "code/desktop_tauri/src-tauri/tauri.conf.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps(
            {
                "productName": "Meeting Copilot",
                "identifier": "com.meetingcopilot.desktop",
            }
        ),
        encoding="utf-8",
    )

    identity = tool.validate_fixed_app_identity(repo, _manifest())

    assert identity["configuration_matches"] is True
    assert identity["stable_install_name"] is True
    assert identity["stable_signing_identity_verified"] is False

    config_path.write_text(
        json.dumps(
            {"productName": "Meeting Copilot Dev", "identifier": "com.example.random"}
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="fixed app identities"):
        tool.validate_fixed_app_identity(repo, _manifest())


def test_build_command_uses_app_bundle_and_explicit_target_dir(tmp_path):
    tool = load_tool_module()
    command = tool.build_command(
        overlay_path=tmp_path / "overlay.json", rust_target_dir=tmp_path / "target"
    )

    assert command[:4] == ["cargo-tauri", "build", "--bundles", "app"]
    assert "--no-sign" in command
    assert "--locked" in command
    assert str(tmp_path / "overlay.json") in command
    assert str(tmp_path / "target") in command


def test_controlled_cargo_tauri_path_infers_sibling_rust_toolchain(tmp_path):
    tool = load_tool_module()
    repo_root = tmp_path / "repo"
    controlled_root = repo_root / "artifacts/tmp/controlled_rust_toolchain"
    cargo_home = controlled_root / "cargo-home"
    rustup_home = controlled_root / "rustup-home"
    cargo_bin = cargo_home / "bin"
    toolchain_bin = rustup_home / "toolchains/stable-arm64-apple-darwin/bin"
    cargo_bin.mkdir(parents=True)
    toolchain_bin.mkdir(parents=True)
    (cargo_bin / "cargo").touch()
    cargo_tauri = cargo_bin / "cargo-tauri"
    cargo_tauri.touch()

    environment = tool.build_toolchain_environment(
        repo_root=repo_root,
        cargo_executable=str(cargo_tauri),
        target_dir=tmp_path / "target",
        base_environment={},
    )

    assert environment["CARGO_HOME"] == str(cargo_home)
    assert environment["RUSTUP_HOME"] == str(rustup_home)
    assert environment["PATH"].split(":")[:2] == [str(toolchain_bin), str(cargo_bin)]
    assert environment["CARGO_TARGET_DIR"] == str(tmp_path / "target")


def test_python_bytecode_cleanup_is_recursive_and_preserves_sources_and_models(
    tmp_path,
):
    tool = load_tool_module()
    runtime_bundle = tmp_path / "MeetingCopilotRuntime.bundle"
    cache = runtime_bundle / "app/backend/package/__pycache__"
    cache.mkdir(parents=True)
    cached_bytecode = cache / "service.cpython-313.pyc"
    cached_bytecode.write_bytes(b"cached-bytecode")
    loose_bytecode = runtime_bundle / "runtime/backend/site-packages/legacy.pyo"
    loose_bytecode.parent.mkdir(parents=True)
    loose_bytecode.write_bytes(b"optimized-bytecode")
    source = runtime_bundle / "app/backend/package/service.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    model = runtime_bundle / "models/funasr-online/model.pt"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"model-weights")

    cleanup = tool.remove_python_bytecode(runtime_bundle)

    assert cleanup == {
        "deleted_file_count": 2,
        "deleted_size_bytes": len(b"cached-bytecode") + len(b"optimized-bytecode"),
        "deleted_pycache_directory_count": 1,
        "bytecode_remaining": 0,
    }
    assert not cache.exists()
    assert not loose_bytecode.exists()
    assert source.read_text(encoding="utf-8") == "VALUE = 1\n"
    assert model.read_bytes() == b"model-weights"


def test_packaged_runtime_bytecode_is_removed_before_signing_and_recorded_in_evidence(
    tmp_path,
    monkeypatch,
):
    tool = load_tool_module()
    repo_root = tmp_path / "repo"
    output_root = repo_root / "artifacts/tmp/packages"
    runtime_bundle = repo_root / "runtime-source/MeetingCopilotRuntime.bundle"
    runtime_bundle.mkdir(parents=True)
    built_app = tmp_path / "target/Meeting Copilot.app"
    built_runtime = built_app / "Contents/Resources/MeetingCopilotRuntime.bundle"
    cache = built_runtime / "app/backend/package/__pycache__"
    cache.mkdir(parents=True)
    (cache / "service.cpython-313.pyc").write_bytes(b"cached-bytecode")
    loose_bytecode = built_runtime / "runtime/backend/site-packages/legacy.pyo"
    loose_bytecode.parent.mkdir(parents=True)
    loose_bytecode.write_bytes(b"optimized-bytecode")
    source_relative = Path("app/backend/package/service.py")
    (built_runtime / source_relative).write_text("VALUE = 1\n", encoding="utf-8")
    model_relative = Path("models/funasr-online/model.pt")
    (built_runtime / model_relative).parent.mkdir(parents=True)
    (built_runtime / model_relative).write_bytes(b"model-weights")
    app_binary = built_app / "Contents/MacOS/meeting-copilot-desktop"
    app_binary.parent.mkdir(parents=True)
    app_binary.write_bytes(b"app-binary")

    prepared_cleanup = {
        "deleted_file_count": 0,
        "deleted_size_bytes": 0,
        "deleted_pycache_directory_count": 0,
        "bytecode_remaining": 0,
    }
    monkeypatch.setattr(
        tool,
        "prepare_runtime_bundle_for_packaging",
        lambda **_kwargs: {
            "manifest": {"fixture": True},
            "file_asr_capability": {"status": "fixture"},
            "diarization_capability": {
                "status": "absent_optional_fail_open",
            },
            "package_decision": {
                "counts_as_internal_controlled_smoke": False,
            },
            "controlled_model_pack": None,
            "controlled_diarization_model_pack": None,
            "python_bytecode_cleanup": prepared_cleanup,
        },
    )
    monkeypatch.setattr(
        tool, "validate_fixed_app_identity", lambda *_args: {"fixture": True}
    )
    monkeypatch.setattr(tool, "resolve_cargo_executable", lambda *_args: "cargo-tauri")
    monkeypatch.setattr(tool, "build_toolchain_environment", lambda **_kwargs: {})
    monkeypatch.setattr(
        tool.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [], 0, stdout="", stderr=""
        ),
    )
    monkeypatch.setattr(tool, "find_built_app", lambda _target_dir: built_app)
    monkeypatch.setattr(
        tool,
        "clone_tree",
        lambda source, destination: shutil.copytree(source, destination, symlinks=True),
    )
    monkeypatch.setattr(
        tool, "validate_runtime_bundle", lambda _bundle: {"fixture": True}
    )

    entitlement_hashes = {
        "main-app": "a" * 64,
        "native-mic": "b" * 64,
    }

    def sign_after_cleanup(app_path, **kwargs):
        runtime = app_path / "Contents/Resources/MeetingCopilotRuntime.bundle"
        assert not list(runtime.rglob("__pycache__"))
        assert not [
            path for path in runtime.rglob("*") if path.suffix in {".pyc", ".pyo"}
        ]
        assert (runtime / source_relative).is_file()
        assert (runtime / model_relative).is_file()
        assert kwargs["mode"] == "ad-hoc"
        assert callable(kwargs["before_step"])
        return {
            "signing_plan": {
                "schema_version": "meeting_copilot.macos_codesign_plan.v1",
                "app_path": str(app_path.resolve()),
                "mode": "ad-hoc",
                "identity": "Developer ID Application: Sensitive Name (SECRETTEAM)",
                "hardened_runtime": True,
                "uses_deep_signing": False,
                "macho_inventory": [
                    {"relative_path": "Contents/MacOS/meeting-copilot-desktop"},
                    {"relative_path": "Contents/Resources/runtime-helper"},
                ],
                "signing_steps": [
                    {
                        "order": 1,
                        "relative_path": "Contents/Resources/runtime-helper",
                        "role": "nested-code",
                        "entitlements_sha256": None,
                        "command": ["codesign", "--sign", "SECRETTEAM", str(app_path)],
                    },
                    {
                        "order": 2,
                        "relative_path": "Contents/Resources/native-mic",
                        "role": "native-mic",
                        "entitlements_sha256": entitlement_hashes["native-mic"],
                        "command": ["codesign", "--sign", "SECRETTEAM", str(app_path)],
                    },
                    {
                        "order": 3,
                        "relative_path": ".",
                        "role": "main-app",
                        "entitlements_sha256": entitlement_hashes["main-app"],
                        "command": ["codesign", "--sign", "SECRETTEAM", str(app_path)],
                    },
                ],
            },
            "signing": {
                "schema_version": "meeting_copilot.macos_codesign_execution_result.v1",
                "status": "passed",
                "signed_target_count": 3,
                "uses_deep_signing": False,
                "hardened_runtime": True,
            },
            "verification": {
                "schema_version": "meeting_copilot.macos_codesign_verification_result.v1",
                "app_path": str(app_path.resolve()),
                "mode": "ad-hoc",
                "identity": "Developer ID Application: Sensitive Name (SECRETTEAM)",
                "uses_deep_verification": False,
                "status": "passed",
                "results": [
                    {
                        "strict_verification_return_code": 0,
                        "runtime_verified": True,
                        "identity_verified": True,
                        "entitlements_verified": True,
                    }
                    for _index in range(3)
                ],
            },
        }

    monkeypatch.setattr(tool.macos_codesign, "sign_and_verify", sign_after_cleanup)

    evidence = tool.package_runtime_app(
        repo_root=repo_root,
        runtime_bundle=runtime_bundle,
        output_root=output_root,
        run_id="bytecode-cleanup",
    )

    cleanup = evidence["python_bytecode_cleanup"]
    assert cleanup["prepared_runtime"] == prepared_cleanup
    assert cleanup["pre_sign_runtime"]["deleted_file_count"] == 2
    assert cleanup["pre_sign_runtime"]["deleted_size_bytes"] == (
        len(b"cached-bytecode") + len(b"optimized-bytecode")
    )
    assert cleanup["deleted_file_count"] == 2
    assert cleanup["deleted_size_bytes"] == len(b"cached-bytecode") + len(
        b"optimized-bytecode"
    )
    assert cleanup["bytecode_remaining"] == 0
    packaged_runtime = (
        output_root
        / "bytecode-cleanup/Meeting Copilot.app/Contents/Resources/MeetingCopilotRuntime.bundle"
    )
    assert (packaged_runtime / source_relative).is_file()
    assert (packaged_runtime / model_relative).read_bytes() == b"model-weights"
    assert (cache / "service.cpython-313.pyc").is_file()
    recorded = json.loads(
        (output_root / "bytecode-cleanup/evidence.json").read_text(encoding="utf-8")
    )
    assert recorded["python_bytecode_cleanup"] == cleanup
    signing = recorded["local_signing"]
    assert signing == evidence["local_signing"]
    assert signing["mode"] == "ad-hoc"
    assert signing["stable_identity_across_builds"] is False
    assert signing["plan_summary"] == {
        "schema_version": "meeting_copilot.macos_codesign_plan.v1",
        "macho_count": 2,
        "signing_step_count": 3,
        "roles": ["main-app", "native-mic", "nested-code"],
        "entitlement_hashes": entitlement_hashes,
        "uses_deep": False,
        "runtime": True,
    }
    assert signing["verification"] == {
        "schema_version": "meeting_copilot.macos_codesign_verification_result.v1",
        "status": "passed",
        "verified_target_count": 3,
        "strict": True,
        "uses_deep": False,
        "runtime": True,
        "identity": True,
        "entitlements": True,
    }
    committed_summary = json.dumps(signing, sort_keys=True)
    assert str(packaged_runtime.parent.parent.parent.resolve()) not in committed_summary
    assert "--sign" not in committed_summary
    assert "SECRETTEAM" not in committed_summary
    assert "Developer ID Application" not in committed_summary


def test_runtime_package_is_not_public_release_evidence():
    tool = load_tool_module()
    assert tool.RUN_ID_PATTERN.fullmatch("packaged-runtime-20260716")
    with pytest.raises(ValueError):
        tool.validate_run_id("../escape")


def test_package_cli_forwards_explicit_diarization_model_options(
    tmp_path, monkeypatch, capsys
):
    tool = load_tool_module()
    captured: dict = {}

    def fake_package_runtime_app(**kwargs):
        captured.update(kwargs)
        return {
            "decision": {
                "counts_as_packaged_runtime_resource_evidence": True,
                "counts_as_public_release": False,
            }
        }

    monkeypatch.setattr(tool, "package_runtime_app", fake_package_runtime_app)
    runtime_bundle = tmp_path / "MeetingCopilotRuntime.bundle"
    output_root = tmp_path / "repo/artifacts/tmp/packages"
    vad = tmp_path / "vad"
    camplus = tmp_path / "camplus"
    policy = tmp_path / "diarization.manifest.json"

    result = tool.main(
        [
            "--repo-root",
            str(tmp_path / "repo"),
            "--runtime-bundle",
            str(runtime_bundle),
            "--output-root",
            str(output_root),
            "--run-id",
            "diarization-forwarding",
            "--diarization-vad-model-dir",
            str(vad),
            "--diarization-camplus-model-dir",
            str(camplus),
            "--diarization-model-pack-manifest",
            str(policy),
        ]
    )

    assert result == 0
    assert captured["diarization_vad_model_dir"] == vad
    assert captured["diarization_camplus_model_dir"] == camplus
    assert captured["diarization_model_pack_manifest"] == policy
    assert captured["runtime_bundle"] == runtime_bundle
    assert (
        json.loads(capsys.readouterr().out)["decision"]["counts_as_public_release"]
        is False
    )
