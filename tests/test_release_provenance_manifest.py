import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "release_provenance_manifest.py"


def load_tool_module():
    spec = importlib.util.spec_from_file_location("release_provenance_manifest", TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def init_release_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "release-test@example.invalid")
    git(repo, "config", "user.name", "Release Test")

    (repo / "LICENSE").write_text("first-party license\n", encoding="utf-8")
    (repo / "NOTICE").write_text("third-party notices\n", encoding="utf-8")
    (repo / "sbom.cdx.json").write_text('{"bomFormat":"CycloneDX"}\n', encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text("print('meeting copilot')\n", encoding="utf-8")
    (repo / "configs").mkdir()
    (repo / "configs" / "release-provenance.json").write_text(
        json.dumps(
            {
                "schema_version": "meeting_copilot.dependency_model_provenance.v1",
                "models": [
                    {
                        "id": "example/model",
                        "immutable_revision": "0123456789abcdef",
                        "artifact_manifest_sha256": "a" * 64,
                        "redistribution_status": "approved",
                    }
                ],
                "binaries": [
                    {
                        "id": "ffmpeg/example",
                        "immutable_revision": "8.1.1-build-1",
                        "artifact_sha256": "b" * 64,
                        "redistribution_status": "approved",
                    }
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    git(repo, "add", "LICENSE", "NOTICE", "sbom.cdx.json", "src", "configs")
    git(repo, "commit", "-m", "initial release source")

    artifact = repo / "artifacts" / "release" / "MeetingCopilot.app.tar"
    evidence = repo / "artifacts" / "evidence" / "run-001" / "manifest.json"
    artifact.parent.mkdir(parents=True)
    evidence.parent.mkdir(parents=True)
    artifact.write_bytes(b"release-artifact")
    evidence.write_text(
        json.dumps(
            {
                "run_id": "run-001",
                "verdict": "go",
                "artifact_path": "artifacts/release/MeetingCopilot.app.tar",
                "artifact_sha256": hashlib.sha256(b"release-artifact").hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    return repo


def build_manifest(tool, repo: Path, **overrides):
    arguments = {
        "repo_root": repo,
        "run_id": "provenance-001",
        "evidence_run_id": "run-001",
        "artifact_path": repo / "artifacts" / "release" / "MeetingCopilot.app.tar",
        "evidence_manifest_path": repo / "artifacts" / "evidence" / "run-001" / "manifest.json",
        "app_metadata": {"name": "Meeting Copilot", "version": "0.1.0"},
    }
    arguments.update(overrides)
    return tool.generate_release_provenance_manifest(**arguments)


def test_clean_release_binds_source_artifact_evidence_and_is_deterministic(tmp_path):
    tool = load_tool_module()
    repo = init_release_repo(tmp_path)

    first = build_manifest(tool, repo)
    second = build_manifest(tool, repo)

    assert first["verdict"] == "go"
    assert first["blockers"] == []
    assert first["git"]["commit"] == git(repo, "rev-parse", "HEAD")
    assert first["git"]["tree"] == git(repo, "rev-parse", "HEAD^{tree}")
    assert first["source"]["tree_sha256"] == second["source"]["tree_sha256"]
    assert first["source"]["file_count"] >= 5
    assert first["artifact"]["sha256"] == hashlib.sha256(b"release-artifact").hexdigest()
    assert first["artifact"]["size_bytes"] == len(b"release-artifact")
    assert first["evidence_manifest"]["declared_run_id"] == "run-001"
    assert first["evidence_manifest"]["run_id_matches"] is True
    assert first["evidence_manifest"]["declared_artifact"]["path_matches"] is True
    assert first["evidence_manifest"]["declared_artifact"]["hash_matches"] is True
    assert first["application"] == {"name": "Meeting Copilot", "version": "0.1.0"}
    assert json.loads((repo / first["manifest_path"]).read_text(encoding="utf-8")) == first | {
        "manifest_path": first["manifest_path"]
    }


def test_dirty_tracked_and_untracked_source_fail_closed(tmp_path):
    tool = load_tool_module()
    repo = init_release_repo(tmp_path)
    (repo / "src" / "app.py").write_text("print('dirty')\n", encoding="utf-8")
    (repo / "tools").mkdir()
    (repo / "tools" / "new_release_step.py").write_text("VALUE = 1\n", encoding="utf-8")

    manifest = build_manifest(tool, repo)

    assert manifest["verdict"] == "no_go"
    assert "dirty_tracked_files" in manifest["blockers"]
    assert "untracked_source_files" in manifest["blockers"]
    assert manifest["git"]["dirty_tracked_files"] == ["src/app.py"]
    assert manifest["git"]["untracked_source_files"] == ["tools/new_release_step.py"]


def test_source_digest_excludes_runtime_artifacts_and_local_secrets(tmp_path):
    tool = load_tool_module()
    repo = init_release_repo(tmp_path)
    first = build_manifest(tool, repo)

    local_config = repo / "configs" / "local" / "llm-gateway.local.json"
    local_config.parent.mkdir(parents=True)
    local_config.write_text('{"api_key":"sk-must-not-be-read"}', encoding="utf-8")
    runtime_file = repo / "code" / "web_mvp" / "backend" / "artifacts" / "tmp" / "runtime.db"
    runtime_file.parent.mkdir(parents=True)
    runtime_file.write_bytes(b"runtime-state")
    second = build_manifest(tool, repo)

    assert second["source"]["tree_sha256"] == first["source"]["tree_sha256"]
    assert second["git"]["untracked_source_files"] == []
    assert "sk-must-not-be-read" not in json.dumps(second)


def test_missing_supply_chain_files_and_unresolved_models_are_blockers(tmp_path):
    tool = load_tool_module()
    repo = init_release_repo(tmp_path)
    (repo / "LICENSE").unlink()
    (repo / "NOTICE").unlink()
    (repo / "sbom.cdx.json").unlink()
    policy_path = repo / "configs" / "release-provenance.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    policy["models"][0]["immutable_revision"] = None
    policy["models"][0]["artifact_manifest_sha256"] = None
    policy["models"][0]["redistribution_status"] = "unresolved"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")

    manifest = build_manifest(tool, repo)

    assert manifest["verdict"] == "no_go"
    assert {
        "root_license_missing",
        "root_notice_missing",
        "sbom_missing",
        "model_revision_unresolved:example/model",
        "model_artifact_manifest_unresolved:example/model",
        "model_redistribution_unapproved:example/model",
    }.issubset(manifest["blockers"])


def test_expected_hash_and_evidence_run_id_mismatch_fail_closed(tmp_path):
    tool = load_tool_module()
    repo = init_release_repo(tmp_path)

    manifest = build_manifest(
        tool,
        repo,
        evidence_run_id="different-run",
        expected_artifact_sha256="0" * 64,
        expected_evidence_sha256="1" * 64,
    )

    assert manifest["verdict"] == "no_go"
    assert "artifact_hash_mismatch" in manifest["blockers"]
    assert "evidence_manifest_hash_mismatch" in manifest["blockers"]
    assert "evidence_run_id_mismatch" in manifest["blockers"]


def test_missing_artifact_and_evidence_are_reported_without_raising(tmp_path):
    tool = load_tool_module()
    repo = init_release_repo(tmp_path)

    manifest = build_manifest(
        tool,
        repo,
        artifact_path=repo / "artifacts" / "release" / "missing.dmg",
        evidence_manifest_path=repo / "artifacts" / "evidence" / "missing.json",
    )

    assert manifest["verdict"] == "no_go"
    assert "artifact_missing" in manifest["blockers"]
    assert "evidence_manifest_missing" in manifest["blockers"]
    assert manifest["artifact"]["sha256"] is None
    assert manifest["evidence_manifest"]["sha256"] is None


def test_repository_scope_rejects_unapproved_paths_and_runtime_scope_is_explicit(tmp_path):
    tool = load_tool_module()
    repo = init_release_repo(tmp_path)
    external_artifact = tmp_path / "external" / "MeetingCopilot.dmg"
    external_artifact.parent.mkdir()
    external_artifact.write_bytes(b"external-artifact")

    rejected = build_manifest(tool, repo, artifact_path=external_artifact)
    accepted = build_manifest(
        tool,
        repo,
        artifact_path=external_artifact,
        artifact_scope="runtime",
    )

    assert "artifact_path_not_approved" in rejected["blockers"]
    assert "artifact_path_not_approved" not in accepted["blockers"]
    assert accepted["artifact"]["scope"] == "runtime"


def test_symlink_inputs_are_rejected_without_hashing_target(tmp_path):
    tool = load_tool_module()
    repo = init_release_repo(tmp_path)
    secret = tmp_path / "secret.txt"
    secret.write_text("sk-never-hash-this", encoding="utf-8")
    artifact = repo / "artifacts" / "release" / "linked.dmg"
    artifact.symlink_to(secret)

    manifest = build_manifest(tool, repo, artifact_path=artifact)

    assert "artifact_symlink_not_allowed" in manifest["blockers"]
    assert manifest["artifact"]["sha256"] is None
    assert "sk-never-hash-this" not in json.dumps(manifest)


def test_evidence_must_describe_the_same_release_artifact(tmp_path):
    tool = load_tool_module()
    repo = init_release_repo(tmp_path)
    evidence = repo / "artifacts" / "evidence" / "run-001" / "manifest.json"
    payload = json.loads(evidence.read_text(encoding="utf-8"))
    payload["artifact_path"] = "artifacts/release/unrelated.dmg"
    payload["artifact_sha256"] = "f" * 64
    evidence.write_text(json.dumps(payload), encoding="utf-8")

    manifest = build_manifest(tool, repo)

    assert manifest["verdict"] == "no_go"
    assert "evidence_artifact_path_mismatch" in manifest["blockers"]
    assert "evidence_artifact_hash_mismatch" in manifest["blockers"]


def test_staged_change_and_tracked_sensitive_config_are_blockers(tmp_path):
    tool = load_tool_module()
    repo = init_release_repo(tmp_path)
    secret_config = repo / "configs" / "local" / "provider.local.json"
    secret_config.parent.mkdir()
    secret_config.write_text('{"api_key":"test-only-value"}', encoding="utf-8")
    git(repo, "add", "configs/local/provider.local.json")

    manifest = build_manifest(tool, repo)

    assert manifest["verdict"] == "no_go"
    assert "dirty_tracked_files" in manifest["blockers"]
    assert "tracked_sensitive_paths" in manifest["blockers"]
    assert manifest["git"]["tracked_sensitive_count"] == 1
    assert "test-only-value" not in json.dumps(manifest)


def test_tracked_env_template_is_not_treated_as_a_secret(tmp_path):
    tool = load_tool_module()
    repo = init_release_repo(tmp_path)
    (repo / ".env.example").write_text("LLM_GATEWAY_API_KEY=replace-me\n", encoding="utf-8")
    git(repo, "add", ".env.example")
    git(repo, "commit", "-m", "document environment template")

    manifest = build_manifest(tool, repo)

    assert manifest["verdict"] == "go"
    assert "tracked_sensitive_paths" not in manifest["blockers"]
    assert manifest["git"]["tracked_sensitive_count"] == 0
    assert tool._is_sensitive_relative_path(Path(".env")) is True
    assert tool._is_sensitive_relative_path(Path(".env.production")) is True


def test_empty_license_and_invalid_sbom_do_not_satisfy_supply_chain_gate(tmp_path):
    tool = load_tool_module()
    repo = init_release_repo(tmp_path)
    (repo / "LICENSE").write_text("", encoding="utf-8")
    (repo / "sbom.cdx.json").write_text("{}", encoding="utf-8")

    manifest = build_manifest(tool, repo)

    assert "root_license_empty" in manifest["blockers"]
    assert "sbom_invalid" in manifest["blockers"]
