from __future__ import annotations

import importlib.util
import hashlib
import json
import os
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "code/asr_runtime/scripts/packaged_file_asr_smoke.py"
FIXTURE_MANIFEST = REPO_ROOT / "code/asr_runtime/file-asr-packaged-smoke-fixtures.manifest.json"


def _load_script():
    spec = importlib.util.spec_from_file_location("packaged_file_asr_smoke", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_package_association_fixture(tmp_path, smoke):
    run_id = "next022-hardened-r2"
    run_root = tmp_path / run_id
    app = run_root / "Meeting Copilot.app"
    runtime = app / "Contents/Resources/MeetingCopilotRuntime.bundle"
    binary = app / "Contents/MacOS/meeting-copilot-desktop"
    component_paths = [
        "bin/file-asr-python",
        "app/transcribe_funasr.py",
        "bin/ffmpeg",
        "licenses/imageio.txt",
        "models/offline",
        "models/vad",
        "models/punc",
    ]
    for relative in component_paths:
        path = runtime / relative
        if relative.startswith("models/"):
            path.mkdir(parents=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fixture")
    launcher = runtime / "bin/meeting-copilot-backend"
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"packaged-app-binary")
    manifest = {
        "schema_version": smoke.RUNTIME_MANIFEST_SCHEMA,
        "component_inventory": {"status": "sealed"},
        "file_asr": {
            "package": {
                "install_status": "bundled",
                "internal_controlled_smoke_status": "internal_controlled_smoke",
                "redistribution_status": "public_redistribution_unresolved",
                "counts_as_public_release": False,
                "sha256": "a" * 64,
                "control_manifest_sha256": "b" * 64,
            },
            "redistribution": {
                "status": "public_redistribution_unresolved",
                "public_redistribution_approved": False,
            },
            "runtime": {"executable": component_paths[0]},
            "worker": {"path": component_paths[1]},
            "converter": {"path": component_paths[2], "license_path": component_paths[3]},
            "models": {
                "offline": {"root": component_paths[4]},
                "vad": {"root": component_paths[5]},
                "punc": {"root": component_paths[6]},
            },
        },
    }
    manifest_bytes = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode()
    manifest_path = runtime / "runtime-bundle-manifest.json"
    manifest_path.write_bytes(manifest_bytes)
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    binary_sha256 = hashlib.sha256(binary.read_bytes()).hexdigest()
    package_payload = {
        "schema_version": "meeting_copilot.tauri_runtime_package.v1",
        "run_id": run_id,
        "app_path": f"artifacts/tmp/{run_id}/{app.name}",
        "fixed_app_identity": {"app_bundle_name": app.name},
        "app_binary": {
            "path": "Contents/MacOS/meeting-copilot-desktop",
            "sha256": binary_sha256,
        },
        "packaged_runtime_manifest": manifest,
        "packaged_runtime_manifest_sha256": manifest_sha256,
        "resource_root_present": True,
        "required_packaged_missing": [],
    }
    package_evidence = run_root / "evidence.json"
    package_evidence.write_text(json.dumps(package_payload), encoding="utf-8")
    return app, runtime, package_evidence, package_payload


def test_runtime_resolution_is_app_relative_and_never_uses_repository_paths(tmp_path):
    smoke = _load_script()
    app = tmp_path / "Renamed Delivery/Meeting Copilot.app"
    runtime = app / "Contents/Resources/MeetingCopilotRuntime.bundle"
    runtime.mkdir(parents=True)

    assert smoke.resolve_runtime_bundle(app) == runtime.resolve()


def test_controlled_fixture_manifest_forbids_fake_quality_claims():
    payload = json.loads(FIXTURE_MANIFEST.read_text(encoding="utf-8"))

    assert payload["quality_scope"]["fake_asr_allowed"] is False
    assert payload["quality_scope"]["counts_as_worker_execution_smoke"] is True
    assert payload["quality_scope"]["counts_as_model_quality_benchmark"] is False
    assert set(payload["fixtures"]) == {"wav", "m4a", "mp3"}


def test_fixture_hash_verification_fails_closed(tmp_path):
    smoke = _load_script()
    supplied = {}
    for extension in smoke.FORMATS:
        path = tmp_path / f"fixture.{extension}"
        path.write_bytes(extension.encode())
        supplied[extension] = path

    with pytest.raises(ValueError, match="fixture hash mismatch"):
        smoke.load_controlled_fixtures(FIXTURE_MANIFEST, supplied)


def test_packaged_child_environment_clears_asr_path_hijacks(tmp_path):
    smoke = _load_script()
    runtime = tmp_path / "Meeting Copilot.app/Contents/Resources/MeetingCopilotRuntime.bundle"
    data_dir = tmp_path / "smoke/data"
    runtime.mkdir(parents=True)
    data_dir.mkdir(parents=True)
    hijacked = {
        "PATH": "/tmp/hijack/bin",
        "SAFE_PARENT_SETTING": "must-not-leak",
        "MEETING_COPILOT_BATCH_FUNASR_PYTHON": "/tmp/hijack/python",
        "MEETING_COPILOT_BATCH_TRANSCRIBE_WORKER": "/tmp/hijack/worker.py",
        "MEETING_COPILOT_BATCH_UNKNOWN_PATH": "/tmp/hijack/anything",
        "MEETING_COPILOT_FILE_ASR_MODEL_DIR": "/tmp/hijack/model",
        "MEETING_COPILOT_FILE_ASR_VAD_MODEL_DIR": "/tmp/hijack/vad",
        "MEETING_COPILOT_FILE_ASR_PUNC_MODEL_DIR": "/tmp/hijack/punc",
        "MEETING_COPILOT_FILE_ASR_UNKNOWN_PATH": "/tmp/hijack/anything-else",
        "MEETING_COPILOT_FUNASR_PYTHON": "/tmp/hijack/realtime-python",
        "MEETING_COPILOT_FUNASR_WORKER": "/tmp/hijack/realtime-worker.py",
        "MEETING_COPILOT_FUNASR_MODEL_DIR": "/tmp/hijack/realtime-model",
        "MEETING_COPILOT_FFMPEG": "/tmp/hijack/ffmpeg",
        "IMAGEIO_FFMPEG_EXE": "/tmp/hijack/imageio-ffmpeg",
        "PYTHONHOME": "/tmp/hijack/python-home",
        "PYTHONPATH": "/tmp/hijack/python-path",
        "HF_HOME": "/tmp/hijack/huggingface",
        "MODELSCOPE_CACHE": "/tmp/hijack/modelscope",
        "HTTP_PROXY": "http://127.0.0.1:9999",
        "HTTPS_PROXY": "http://127.0.0.1:9999",
        "ALL_PROXY": "http://127.0.0.1:9999",
        "NO_PROXY": "*",
    }

    environment, controls = smoke.build_packaged_child_environment(
        base_env=hijacked,
        runtime_bundle=runtime,
        data_dir=data_dir,
        port=43123,
        token="test-token",
    )

    assert environment["MEETING_COPILOT_RUNTIME_MANIFEST"] == str(
        runtime / "runtime-bundle-manifest.json"
    )
    assert environment["MEETING_COPILOT_PORT"] == "43123"
    assert environment["PATH"] == "/usr/bin:/bin:/usr/sbin:/sbin"
    assert "SAFE_PARENT_SETTING" not in environment
    assert not any(
        name.startswith(("MEETING_COPILOT_BATCH_", "MEETING_COPILOT_FILE_ASR_"))
        for name in environment
    )
    assert not any(name.startswith("MEETING_COPILOT_FUNASR_") for name in environment)
    assert "MEETING_COPILOT_FFMPEG" not in environment
    assert "IMAGEIO_FFMPEG_EXE" not in environment
    assert "PYTHONHOME" not in environment
    assert "PYTHONPATH" not in environment
    assert "HF_HOME" not in environment
    assert "MODELSCOPE_CACHE" not in environment
    assert "HTTP_PROXY" not in environment
    assert "HTTPS_PROXY" not in environment
    assert "ALL_PROXY" not in environment
    assert environment["NO_PROXY"] == "127.0.0.1,localhost"
    assert set(hijacked) <= (
        set(controls["removed_parent_names"]) | set(controls["overridden_parent_names"])
    )


def test_packaged_smoke_names_only_resource_bundle_and_direct_backend_api():
    smoke = _load_script()

    assert smoke.SMOKE_NAME == "app resource bundle + direct backend API file ASR smoke"
    assert smoke.SMOKE_CLAIM_SCOPE == {
        "app_resource_bundle": True,
        "direct_backend_api": True,
        "tauri_supervisor": False,
        "rust_supervisor": False,
    }


def test_usage_flags_are_derived_from_controls_not_claimed_constants(tmp_path):
    smoke = _load_script()
    runtime = tmp_path / "Meeting Copilot.app/Contents/Resources/MeetingCopilotRuntime.bundle"
    runtime.mkdir(parents=True)
    launcher = runtime / "bin/meeting-copilot-backend"
    converter = runtime / "runtime/backend-venv/lib/imageio-ffmpeg"
    worker = runtime / "app/transcribe_funasr.py"
    launcher.parent.mkdir(parents=True)
    converter.parent.mkdir(parents=True)
    worker.parent.mkdir(parents=True)
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    converter.write_bytes(b"ffmpeg")
    worker.write_text("worker\n", encoding="utf-8")
    environment = {
        "MEETING_COPILOT_RUNTIME_MANIFEST": str(runtime / "runtime-bundle-manifest.json"),
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
    }
    controls = smoke.derive_usage_flags(
        runtime_bundle=runtime,
        component_paths={"worker": worker, "converter": converter},
        backend_command=["/bin/sh", str(launcher)],
        child_environment=environment,
        fixture_policy={"quality_scope": {"fake_asr_allowed": False}},
        provider_health={
            "asr": {"file_provider": "local_funasr_batch", "file_asr_available": True},
            "remote_asr": {"default_enabled": False, "enabled": False, "providers": []},
        },
        network_boundary={
            "verification_status": "verified",
            "allowed_destinations": [{"host": "127.0.0.1", "port": 43123}],
            "remote_asr_observations": [],
            "proxy_environment_absent": True,
            "offline_environment": True,
            "remote_asr_provider_disabled": True,
        },
        runtime_logs='{"event": "ffmpeg.converted"}\n',
        conversion_events={"m4a": True, "mp3": True},
    )

    assert controls == {
        "fake_asr_used": False,
        "remote_asr_used": False,
        "global_ffmpeg_used": False,
    }

    fail_closed = smoke.derive_usage_flags(
        runtime_bundle=runtime,
        component_paths={"worker": worker, "converter": converter},
        backend_command=["/bin/sh", str(launcher)],
        child_environment=environment | {
            "MEETING_COPILOT_BATCH_TRANSCRIBE_WORKER": "/tmp/hijack.py"
        },
        fixture_policy={"quality_scope": {"fake_asr_allowed": False}},
        provider_health={
            "asr": {"file_provider": "local_funasr_batch", "file_asr_available": True},
            "remote_asr": {"default_enabled": False, "enabled": False, "providers": []},
        },
        network_boundary={"verification_status": "unverified"},
        runtime_logs="",
        conversion_events={"m4a": True, "mp3": True},
    )

    assert fail_closed["fake_asr_used"] is True
    assert fail_closed["remote_asr_used"] is True


def test_verified_package_association_and_report_paths_are_app_relative(tmp_path):
    smoke = _load_script()
    app, runtime, package_evidence, _payload = _write_package_association_fixture(
        tmp_path, smoke
    )

    association = smoke.verify_package_evidence(
        package_evidence_path=package_evidence,
        app_path=app,
        runtime_manifest_path=runtime / "runtime-bundle-manifest.json",
    )
    reported_command = smoke.report_backend_command(
        app_path=app,
        backend_command=smoke.packaged_backend_command(runtime),
    )
    report = {
        "app_bundle_name": app.name,
        "app_binary": smoke.app_binary_evidence(app),
        "package_evidence": association,
        "execution": {"backend_command": reported_command},
    }
    smoke.validate_report_safety(report)
    serialized = json.dumps(report, sort_keys=True)

    assert association["verification_status"] == "verified"
    assert association["run_id"] == "next022-hardened-r2"
    assert association["app_bundle_name"] == app.name
    assert association["app_binary"]["path"] == "Contents/MacOS/meeting-copilot-desktop"
    assert association["packaged_runtime_manifest"]["path"] == (
        "Contents/Resources/MeetingCopilotRuntime.bundle/runtime-bundle-manifest.json"
    )
    assert reported_command == [
        "Contents/Resources/MeetingCopilotRuntime.bundle/bin/meeting-copilot-backend"
    ]
    assert str(tmp_path) not in serialized
    assert "/Users/" not in serialized
    assert "Bearer " not in serialized


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("schema", "schema"),
        ("app_binary_sha256", "app binary sha256 mismatch"),
        ("runtime_manifest_sha256", "runtime manifest sha256 mismatch"),
        ("run_id", "run_id"),
        ("app_bundle_name", "app bundle name"),
    ],
)
def test_package_evidence_mismatches_fail_closed(tmp_path, mutation, message):
    smoke = _load_script()
    app, runtime, package_evidence, payload = _write_package_association_fixture(tmp_path, smoke)
    if mutation == "schema":
        payload["schema_version"] = "wrong.schema"
    elif mutation == "app_binary_sha256":
        payload["app_binary"]["sha256"] = "0" * 64
    elif mutation == "runtime_manifest_sha256":
        payload["packaged_runtime_manifest_sha256"] = "0" * 64
    elif mutation == "run_id":
        payload["run_id"] = "different-run"
    else:
        payload["app_path"] = "artifacts/tmp/next022-hardened-r2/Other.app"
    package_evidence.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        smoke.verify_package_evidence(
            package_evidence_path=package_evidence,
            app_path=app,
            runtime_manifest_path=runtime / "runtime-bundle-manifest.json",
        )


def test_missing_package_evidence_is_non_acceptance_without_running_backend(tmp_path):
    smoke = _load_script()
    app, _runtime, _package_evidence, _payload = _write_package_association_fixture(
        tmp_path, smoke
    )
    output = tmp_path / "smoke-output"

    evidence = smoke.run_smoke(
        app_path=app,
        package_evidence_path=None,
        fixture_manifest=tmp_path / "not-read.json",
        fixtures={extension: tmp_path / f"not-read.{extension}" for extension in smoke.FORMATS},
        output_dir=output,
    )

    assert evidence["status"] == "no_go_package_evidence_unverified"
    assert evidence["package_evidence"]["verification_status"] == "unverified"
    assert evidence["counts_as_packaged_file_asr_plumbing_evidence"] is False
    assert evidence["counts_as_real_model_execution_smoke"] is False
    assert "results" not in evidence
    assert str(tmp_path) not in json.dumps(evidence, sort_keys=True)


def test_cli_accepts_optional_package_evidence():
    smoke = _load_script()

    args = smoke.parse_args([
        "--app", "Meeting Copilot.app",
        "--package-evidence", "evidence.json",
        "--fixture-manifest", "fixtures.json",
        "--wav", "fixture.wav",
        "--m4a", "fixture.m4a",
        "--mp3", "fixture.mp3",
        "--output-dir", "output",
    ])

    assert args.package_evidence == Path("evidence.json")


def test_cli_returns_nonzero_for_package_evidence_no_go(monkeypatch, capsys):
    smoke = _load_script()
    monkeypatch.setattr(
        smoke,
        "run_smoke",
        lambda **_kwargs: {"status": "no_go_package_evidence_unverified"},
    )

    exit_code = smoke.main([
        "--app", "Meeting Copilot.app",
        "--fixture-manifest", "fixtures.json",
        "--wav", "fixture.wav",
        "--m4a", "fixture.m4a",
        "--mp3", "fixture.mp3",
        "--output-dir", "output",
    ])

    assert exit_code == 2
    assert json.loads(capsys.readouterr().out)["status"] == (
        "no_go_package_evidence_unverified"
    )


def test_report_safety_rejects_absolute_paths_and_bearer_secrets():
    smoke = _load_script()

    with pytest.raises(ValueError, match="absolute path"):
        smoke.validate_report_safety({"app_path": "/Users/chase/Meeting Copilot.app"})
    with pytest.raises(ValueError, match="absolute path"):
        smoke.validate_report_safety({"message": "launcher is /private/tmp/app/bin/backend"})
    with pytest.raises(ValueError, match="Bearer secret"):
        smoke.validate_report_safety({"authorization": "Bearer secret-token"})


def test_packaged_manifest_requires_internal_smoke_and_keeps_public_redistribution_unresolved(
    tmp_path,
):
    smoke = _load_script()
    runtime = tmp_path / "MeetingCopilotRuntime.bundle"
    component_paths = [
        "bin/file-asr-python",
        "app/transcribe_funasr.py",
        "bin/ffmpeg",
        "licenses/imageio.txt",
        "models/offline",
        "models/vad",
        "models/punc",
    ]
    for relative in component_paths:
        path = runtime / relative
        if relative.startswith("models/"):
            path.mkdir(parents=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fixture")
    manifest = {
        "schema_version": smoke.RUNTIME_MANIFEST_SCHEMA,
        "component_inventory": {"status": "sealed"},
        "file_asr": {
            "package": {
                "install_status": "bundled",
                "internal_controlled_smoke_status": "internal_controlled_smoke",
                "redistribution_status": "public_redistribution_unresolved",
                "counts_as_public_release": False,
            },
            "redistribution": {
                "status": "public_redistribution_unresolved",
                "public_redistribution_approved": False,
            },
            "runtime": {"executable": component_paths[0]},
            "worker": {"path": component_paths[1]},
            "converter": {"path": component_paths[2], "license_path": component_paths[3]},
            "models": {
                "offline": {"root": component_paths[4]},
                "vad": {"root": component_paths[5]},
                "punc": {"root": component_paths[6]},
            },
        },
    }
    manifest_path = runtime / "runtime-bundle-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    assert smoke.load_packaged_manifest(runtime)["file_asr"]["package"][
        "internal_controlled_smoke_status"
    ] == "internal_controlled_smoke"

    manifest["file_asr"]["package"]["redistribution_status"] = "approved"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="public redistribution boundary"):
        smoke.load_packaged_manifest(runtime)


@pytest.mark.skipif(
    not os.environ.get("MEETING_COPILOT_NEXT022_APP")
    or not os.environ.get("MEETING_COPILOT_NEXT022_PACKAGE_EVIDENCE"),
    reason=(
        "set MEETING_COPILOT_NEXT022_APP and MEETING_COPILOT_NEXT022_PACKAGE_EVIDENCE "
        "to run the multi-gigabyte real packaged model smoke"
    ),
)
def test_real_packaged_wav_m4a_mp3_worker_and_persistence(tmp_path):
    smoke = _load_script()
    fixture_root = Path(os.environ["MEETING_COPILOT_NEXT022_FIXTURE_ROOT"])
    evidence = smoke.run_smoke(
        app_path=Path(os.environ["MEETING_COPILOT_NEXT022_APP"]),
        package_evidence_path=Path(os.environ["MEETING_COPILOT_NEXT022_PACKAGE_EVIDENCE"]),
        fixture_manifest=FIXTURE_MANIFEST,
        fixtures={
            "wav": fixture_root / "asr-example.wav",
            "m4a": fixture_root / "asr-example.m4a",
            "mp3": fixture_root / "asr-example.mp3",
        },
        output_dir=tmp_path / "smoke",
    )

    assert evidence["status"] == "passed"
    assert evidence["package_evidence"]["verification_status"] == "verified"
    assert "app_path" not in evidence
    assert not any(value.startswith("/") for value in evidence["execution"]["backend_command"])
    assert evidence["fake_asr_used"] is False
    assert evidence["counts_as_real_model_execution_smoke"] is True
    assert evidence["counts_as_model_quality_benchmark"] is False
    assert set(evidence["results"]) == {"wav", "m4a", "mp3"}
