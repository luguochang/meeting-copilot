#!/usr/bin/env python3
"""Bind a release artifact and its evidence to the exact source tree.

The gate is intentionally fail-closed. It can describe a dirty development
worktree, but it cannot declare that worktree releasable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "artifacts" / "tmp" / "release_provenance"
DEFAULT_PROVENANCE_POLICY = REPO_ROOT / "configs" / "release-provenance.json"
SCHEMA_VERSION = "meeting_copilot.release_provenance.v1"
HASH_ALGORITHM = "sha256"
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

APPROVED_REPOSITORY_ARTIFACT_ROOTS = (
    Path("artifacts"),
    Path("code/web_mvp/backend/artifacts"),
    Path("code/desktop_tauri/target/release/bundle"),
    Path("code/asr_runtime/outputs"),
)
UNTRACKED_SOURCE_ROOTS = {
    ".github",
    "code",
    "configs",
    "data",
    "docs",
    "tests",
    "tools",
    "设计稿",
}
UNTRACKED_SOURCE_SUFFIXES = {
    ".c",
    ".cc",
    ".cfg",
    ".cpp",
    ".css",
    ".go",
    ".h",
    ".html",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".lock",
    ".md",
    ".mjs",
    ".py",
    ".rs",
    ".scss",
    ".sh",
    ".sql",
    ".toml",
    ".ts",
    ".tsx",
    ".yaml",
    ".yml",
}
EXCLUDED_DIRECTORY_NAMES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    ".venv-funasr",
    ".venv-sherpa",
    "__pycache__",
    "build",
    "dist",
    "models",
    "node_modules",
    "outputs",
    "target",
}
SENSITIVE_FILE_PATTERNS = (
    re.compile(r"^\.env(?:\..+)?$", re.IGNORECASE),
    re.compile(r".*\.local\.json$", re.IGNORECASE),
    re.compile(r".*\.secret\.json$", re.IGNORECASE),
)
SAFE_ENV_TEMPLATE_NAMES = {
    ".env.example",
    ".env.sample",
    ".env.template",
}


def generate_release_provenance_manifest(
    *,
    repo_root: Path,
    run_id: str,
    evidence_run_id: str,
    artifact_path: Path,
    evidence_manifest_path: Path,
    artifact_scope: str = "repository",
    evidence_scope: str = "repository",
    expected_artifact_sha256: str | None = None,
    expected_evidence_sha256: str | None = None,
    app_metadata: dict[str, str] | None = None,
    provenance_policy_path: Path | None = None,
) -> dict[str, Any]:
    """Generate and persist one deterministic release provenance decision."""

    repo_root = _absolute_lexical(repo_root)
    _validate_run_id(run_id, field="run_id")
    _validate_run_id(evidence_run_id, field="evidence_run_id")
    expected_artifact_sha256 = _normalize_expected_hash(expected_artifact_sha256, "artifact")
    expected_evidence_sha256 = _normalize_expected_hash(expected_evidence_sha256, "evidence_manifest")

    blockers: list[str] = []
    git_state = _inspect_git_state(repo_root, blockers)
    source = _source_tree_digest(repo_root, git_state["tracked_files"], git_state["untracked_source_files"])
    artifact = _inspect_bound_file(
        repo_root=repo_root,
        path=artifact_path,
        scope=artifact_scope,
        kind="artifact",
        expected_sha256=expected_artifact_sha256,
        blockers=blockers,
    )
    evidence = _inspect_bound_file(
        repo_root=repo_root,
        path=evidence_manifest_path,
        scope=evidence_scope,
        kind="evidence_manifest",
        expected_sha256=expected_evidence_sha256,
        blockers=blockers,
    )
    _bind_evidence(
        evidence,
        artifact=artifact,
        repo_root=repo_root,
        evidence_run_id=evidence_run_id,
        blockers=blockers,
    )
    artifact.pop("_absolute_path", None)

    policy_path = provenance_policy_path or repo_root / "configs" / "release-provenance.json"
    supply_chain = _inspect_supply_chain(repo_root, policy_path=policy_path, blockers=blockers)

    if git_state["dirty_tracked_files"]:
        blockers.append("dirty_tracked_files")
    if git_state["untracked_source_files"]:
        blockers.append("untracked_source_files")
    if git_state["tracked_sensitive_count"]:
        blockers.append("tracked_sensitive_paths")

    output_path = _output_path(repo_root, run_id)
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "verdict": "no_go" if blockers else "go",
        "blockers": sorted(set(blockers)),
        "run_id": run_id,
        "evidence_run_id": evidence_run_id,
        "git": {
            "commit": git_state["commit"],
            "tree": git_state["tree"],
            "dirty_tracked_count": len(git_state["dirty_tracked_files"]),
            "dirty_tracked_files": git_state["dirty_tracked_files"],
            "untracked_source_count": len(git_state["untracked_source_files"]),
            "untracked_source_files": git_state["untracked_source_files"],
            "tracked_sensitive_count": git_state["tracked_sensitive_count"],
        },
        "source": source,
        "artifact": artifact,
        "evidence_manifest": evidence,
        "application": dict(sorted((app_metadata or {}).items())),
        "supply_chain": supply_chain,
        "privacy_cost_flags": {
            "secret_values_read": False,
            "configs_local_read": False,
            "remote_service_called": False,
        },
        "manifest_path": output_path.relative_to(repo_root).as_posix(),
    }
    _write_json_atomic(output_path, manifest)
    return manifest


def _inspect_git_state(repo_root: Path, blockers: list[str]) -> dict[str, Any]:
    commit = _git_text(repo_root, "rev-parse", "HEAD")
    tree = _git_text(repo_root, "rev-parse", "HEAD^{tree}")
    if not commit:
        blockers.append("git_commit_unavailable")
    if not tree:
        blockers.append("git_tree_unavailable")

    tracked_files = _git_paths(repo_root, "ls-files", "-z")
    dirty_tracked = sorted(
        set(_git_paths(repo_root, "diff", "--name-only", "-z"))
        | set(_git_paths(repo_root, "diff", "--cached", "--name-only", "-z"))
    )
    untracked = _git_paths(repo_root, "ls-files", "--others", "--exclude-standard", "-z")
    untracked_source = sorted(path for path in untracked if _is_untracked_source_path(Path(path)))
    tracked_sensitive = [path for path in tracked_files if _is_sensitive_relative_path(Path(path))]
    return {
        "commit": commit or None,
        "tree": tree or None,
        "tracked_files": tracked_files,
        "dirty_tracked_files": [_safe_display_source_path(Path(path)) for path in dirty_tracked],
        "untracked_source_files": [_safe_display_source_path(Path(path)) for path in untracked_source],
        "tracked_sensitive_count": len(tracked_sensitive),
    }


def _source_tree_digest(repo_root: Path, tracked_files: list[str], untracked_source_files: list[str]) -> dict[str, Any]:
    selected = sorted(
        {
            path
            for path in [*tracked_files, *untracked_source_files]
            if not _is_excluded_source_path(Path(path))
        }
    )
    tree_hash = hashlib.sha256()
    hashed_count = 0
    missing_count = 0
    symlink_count = 0
    for relative_text in selected:
        relative = Path(relative_text)
        absolute = repo_root / relative
        path_bytes = relative.as_posix().encode("utf-8", errors="surrogateescape")
        if absolute.is_symlink():
            payload = os.readlink(absolute).encode("utf-8", errors="surrogateescape")
            kind = b"symlink"
            mode = b"120000"
            symlink_count += 1
        elif absolute.is_file():
            payload = _sha256_file(absolute).encode("ascii")
            kind = b"file"
            mode = b"100755" if absolute.stat().st_mode & 0o111 else b"100644"
        else:
            payload = b"missing"
            kind = b"missing"
            mode = b"000000"
            missing_count += 1
        tree_hash.update(kind + b"\0" + mode + b"\0" + path_bytes + b"\0" + payload + b"\n")
        hashed_count += 1
    return {
        "algorithm": "sha256-mode-path-content-v2",
        "tree_sha256": tree_hash.hexdigest(),
        "file_count": hashed_count,
        "missing_file_count": missing_count,
        "symlink_count": symlink_count,
        "exclusions": [
            "artifacts and runtime data",
            "build/dist/target/node_modules/venv/cache directories",
            "configs/local and secret-like local config files",
        ],
    }


def _inspect_bound_file(
    *,
    repo_root: Path,
    path: Path,
    scope: str,
    kind: str,
    expected_sha256: str | None,
    blockers: list[str],
) -> dict[str, Any]:
    candidate = path if path.is_absolute() else repo_root / path
    candidate = _absolute_lexical(candidate)
    display_path = _display_path(candidate, repo_root)
    result: dict[str, Any] = {
        "_absolute_path": str(candidate),
        "scope": scope,
        "path": display_path,
        "exists": False,
        "size_bytes": None,
        "sha256": None,
        "expected_sha256": expected_sha256,
        "hash_matches": None,
    }

    if scope not in {"repository", "runtime"}:
        blockers.append(f"{kind}_scope_invalid")
        return result
    if _is_sensitive_repo_path(candidate, repo_root):
        blockers.append(f"{kind}_sensitive_path_not_allowed")
        return result
    if _path_contains_symlink(candidate):
        blockers.append(f"{kind}_symlink_not_allowed")
        return result
    if scope == "repository" and not _is_approved_repository_artifact(candidate, repo_root):
        blockers.append(f"{kind}_path_not_approved")
        return result
    if not candidate.exists():
        blockers.append(f"{kind}_missing")
        return result
    try:
        mode = candidate.stat().st_mode
    except OSError:
        blockers.append(f"{kind}_unreadable")
        return result
    if not stat.S_ISREG(mode):
        blockers.append(f"{kind}_not_regular_file")
        return result

    digest = _sha256_file(candidate)
    result.update(
        {
            "exists": True,
            "size_bytes": candidate.stat().st_size,
            "sha256": digest,
            "hash_matches": expected_sha256 is None or digest == expected_sha256,
        }
    )
    if expected_sha256 is not None and digest != expected_sha256:
        blockers.append(f"{kind}_hash_mismatch")
    return result


def _bind_evidence(
    evidence: dict[str, Any],
    *,
    artifact: dict[str, Any],
    repo_root: Path,
    evidence_run_id: str,
    blockers: list[str],
) -> None:
    evidence["declared_run_id"] = None
    evidence["run_id_matches"] = None
    absolute_path = Path(str(evidence.pop("_absolute_path", evidence["path"])))
    if not evidence["exists"] or evidence["sha256"] is None:
        return
    try:
        payload = json.loads(absolute_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        blockers.append("evidence_manifest_invalid_json")
        return
    if not isinstance(payload, dict):
        blockers.append("evidence_manifest_invalid_json")
        return
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    declared = payload.get("evidence_run_id") or payload.get("run_id") or metadata.get("run_id")
    if not isinstance(declared, str) or not declared.strip():
        blockers.append("evidence_run_id_missing")
        return
    evidence["declared_run_id"] = declared
    evidence["run_id_matches"] = declared == evidence_run_id
    if declared != evidence_run_id:
        blockers.append("evidence_run_id_mismatch")

    evidence_verdict = payload.get("verdict")
    evidence_status = payload.get("status")
    public_release_flag = payload.get("counts_as_public_release_package")
    base_go = evidence_verdict == "go" or (
        isinstance(evidence_status, str) and evidence_status.startswith("go_")
    )
    allows_release = base_go and public_release_flag is not False
    evidence["decision"] = {
        "verdict": evidence_verdict,
        "status": evidence_status,
        "counts_as_public_release_package": public_release_flag,
        "allows_release": allows_release,
    }
    if not allows_release:
        blockers.append("evidence_decision_not_release_go")

    nested_artifact = payload.get("artifact") if isinstance(payload.get("artifact"), dict) else {}
    declared_artifact_path = (
        payload.get("artifact_path")
        or payload.get("dmg_path")
        or nested_artifact.get("path")
    )
    declared_artifact_hash = (
        payload.get("artifact_sha256")
        or payload.get("dmg_sha256")
        or nested_artifact.get("sha256")
    )
    path_matches = False
    if isinstance(declared_artifact_path, str) and declared_artifact_path:
        declared_path = Path(declared_artifact_path)
        if not declared_path.is_absolute():
            declared_path = repo_root / declared_path
        path_matches = _absolute_lexical(declared_path) == Path(str(artifact.get("_absolute_path", "")))
    else:
        blockers.append("evidence_artifact_path_missing")
    if declared_artifact_path and not path_matches:
        blockers.append("evidence_artifact_path_mismatch")

    hash_matches = bool(
        isinstance(declared_artifact_hash, str)
        and artifact.get("sha256")
        and declared_artifact_hash.lower() == artifact["sha256"]
    )
    if not isinstance(declared_artifact_hash, str) or not declared_artifact_hash:
        blockers.append("evidence_artifact_hash_missing")
    elif not hash_matches:
        blockers.append("evidence_artifact_hash_mismatch")
    evidence["declared_artifact"] = {
        "path": declared_artifact_path,
        "sha256": declared_artifact_hash,
        "path_matches": path_matches,
        "hash_matches": hash_matches,
    }


def _inspect_supply_chain(repo_root: Path, *, policy_path: Path, blockers: list[str]) -> dict[str, Any]:
    license_path = _first_root_file(repo_root, ("LICENSE", "LICENSE.txt", "LICENSE.md"))
    notice_path = _first_root_file(repo_root, ("NOTICE", "NOTICE.txt", "NOTICE.md"))
    sbom_path = _first_root_file(
        repo_root,
        ("sbom.json", "sbom.cdx.json", "sbom.spdx.json", "bom.json", "SBOM.json"),
    )
    if license_path is None:
        blockers.append("root_license_missing")
    elif license_path.stat().st_size == 0:
        blockers.append("root_license_empty")
    if notice_path is None:
        blockers.append("root_notice_missing")
    elif notice_path.stat().st_size == 0:
        blockers.append("root_notice_empty")
    if sbom_path is None:
        blockers.append("sbom_missing")
    elif not _valid_sbom(sbom_path):
        blockers.append("sbom_invalid")

    policy_path = policy_path if policy_path.is_absolute() else repo_root / policy_path
    policy_path = _absolute_lexical(policy_path)
    models: list[dict[str, Any]] = []
    binaries: list[dict[str, Any]] = []
    policy_status = "missing"
    if _is_sensitive_repo_path(policy_path, repo_root) or _path_contains_symlink(policy_path):
        blockers.append("dependency_model_provenance_manifest_path_invalid")
        policy_status = "invalid_path"
    elif not policy_path.is_file():
        blockers.append("dependency_model_provenance_manifest_missing")
    else:
        try:
            policy = json.loads(policy_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            blockers.append("dependency_model_provenance_manifest_invalid")
            policy_status = "invalid"
        else:
            if not isinstance(policy, dict):
                blockers.append("dependency_model_provenance_manifest_invalid")
                policy_status = "invalid"
            else:
                policy_status = "loaded"
                models = _validate_components(
                    policy.get("models"),
                    component_kind="model",
                    hash_field="artifact_manifest_sha256",
                    blockers=blockers,
                )
                binaries = _validate_components(
                    policy.get("binaries"),
                    component_kind="binary",
                    hash_field="artifact_sha256",
                    blockers=blockers,
                )

    return {
        "root_license": _display_optional_root_path(license_path, repo_root),
        "root_notice": _display_optional_root_path(notice_path, repo_root),
        "sbom": _display_optional_root_path(sbom_path, repo_root),
        "provenance_policy": {
            "path": _display_path(policy_path, repo_root),
            "status": policy_status,
        },
        "models": models,
        "binaries": binaries,
    }


def _validate_components(
    raw_components: Any,
    *,
    component_kind: str,
    hash_field: str,
    blockers: list[str],
) -> list[dict[str, Any]]:
    if not isinstance(raw_components, list) or not raw_components:
        blockers.append(f"{component_kind}_provenance_missing")
        return []
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_components):
        item = raw if isinstance(raw, dict) else {}
        component_id = item.get("id") if isinstance(item.get("id"), str) else f"index-{index}"
        revision = item.get("immutable_revision")
        artifact_hash = item.get(hash_field)
        redistribution = item.get("redistribution_status")
        revision_is_resolved = isinstance(revision, str) and bool(revision.strip())
        if component_kind == "model" and revision_is_resolved:
            revision_is_resolved = bool(re.fullmatch(r"[0-9a-fA-F]{7,64}", revision))
        elif revision_is_resolved:
            revision_is_resolved = revision.lower() not in {"main", "master", "latest"}
        if not revision_is_resolved:
            blockers.append(f"{component_kind}_revision_unresolved:{component_id}")
        if not isinstance(artifact_hash, str) or not SHA256_PATTERN.fullmatch(artifact_hash.lower()):
            blocker_name = (
                "model_artifact_manifest_unresolved"
                if component_kind == "model"
                else "binary_artifact_hash_unresolved"
            )
            blockers.append(f"{blocker_name}:{component_id}")
        if redistribution != "approved":
            blockers.append(f"{component_kind}_redistribution_unapproved:{component_id}")
        normalized.append(
            {
                "id": component_id,
                "immutable_revision": revision,
                hash_field: artifact_hash,
                "redistribution_status": redistribution or "unresolved",
            }
        )
    return normalized


def _is_untracked_source_path(path: Path) -> bool:
    if _is_excluded_source_path(path):
        return False
    parts = path.parts
    if not parts:
        return False
    if parts[0] in UNTRACKED_SOURCE_ROOTS:
        return path.suffix.lower() in UNTRACKED_SOURCE_SUFFIXES or path.name in {
            "Cargo.lock",
            "Cargo.toml",
            "Dockerfile",
            "Makefile",
        }
    return len(parts) == 1 and (
        path.suffix.lower() in UNTRACKED_SOURCE_SUFFIXES
        or path.name in {"Dockerfile", "Makefile", "README", "LICENSE", "NOTICE"}
    )


def _is_excluded_source_path(path: Path) -> bool:
    parts = path.parts
    if not parts:
        return True
    if _is_sensitive_relative_path(path):
        return True
    if parts[0] == "artifacts":
        return True
    if "artifacts" in parts and "tmp" in parts[parts.index("artifacts") + 1 :]:
        return True
    if any(part in EXCLUDED_DIRECTORY_NAMES for part in parts):
        return True
    if parts[:3] == ("data", "asr_eval", "generated"):
        return True
    if parts[:3] == ("data", "asr_eval", "local_samples"):
        return True
    if parts[:3] == ("data", "asr_eval", "public_raw"):
        return True
    return False


def _is_sensitive_relative_path(path: Path) -> bool:
    parts = path.parts
    if len(parts) >= 2 and parts[:2] == ("configs", "local"):
        return True
    if path.name.lower() in SAFE_ENV_TEMPLATE_NAMES:
        return False
    return any(pattern.fullmatch(path.name) for pattern in SENSITIVE_FILE_PATTERNS)


def _is_sensitive_repo_path(path: Path, repo_root: Path) -> bool:
    try:
        relative = path.relative_to(repo_root)
    except ValueError:
        return False
    return _is_sensitive_relative_path(relative)


def _is_approved_repository_artifact(path: Path, repo_root: Path) -> bool:
    return any(_is_within(path, repo_root / root) for root in APPROVED_REPOSITORY_ARTIFACT_ROOTS)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(_absolute_lexical(root))
    except ValueError:
        return False
    return True


def _path_contains_symlink(path: Path) -> bool:
    absolute = _absolute_lexical(path)
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _first_root_file(repo_root: Path, names: Iterable[str]) -> Path | None:
    for name in names:
        candidate = repo_root / name
        if candidate.is_file() and not candidate.is_symlink():
            return candidate
    return None


def _valid_sbom(path: Path) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict) or not payload:
        return False
    return payload.get("bomFormat") == "CycloneDX" or isinstance(payload.get("spdxVersion"), str)


def _display_optional_root_path(path: Path | None, repo_root: Path) -> str | None:
    return _display_path(path, repo_root) if path else None


def _display_path(path: Path, repo_root: Path) -> str:
    try:
        return path.relative_to(repo_root).as_posix()
    except ValueError:
        return str(path)


def _safe_display_source_path(path: Path) -> str:
    return "<sensitive-path>" if _is_sensitive_relative_path(path) else path.as_posix()


def _absolute_lexical(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _git_text(repo_root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip()


def _git_paths(repo_root: Path, *args: str) -> list[str]:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            check=True,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return [item.decode("utf-8", errors="surrogateescape") for item in result.stdout.split(b"\0") if item]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_expected_hash(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    normalized = value.lower().strip()
    if not SHA256_PATTERN.fullmatch(normalized):
        raise ValueError(f"expected_{field}_sha256 must be 64 lowercase hexadecimal characters")
    return normalized


def _validate_run_id(value: str, *, field: str) -> None:
    if not RUN_ID_PATTERN.fullmatch(value):
        raise ValueError(f"{field} must match {RUN_ID_PATTERN.pattern}")


def _output_path(repo_root: Path, run_id: str) -> Path:
    return repo_root / "artifacts" / "tmp" / "release_provenance" / run_id / "manifest.json"


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--evidence-run-id", required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--evidence-manifest", type=Path, required=True)
    parser.add_argument("--artifact-scope", choices=("repository", "runtime"), default="repository")
    parser.add_argument("--evidence-scope", choices=("repository", "runtime"), default="repository")
    parser.add_argument("--expected-artifact-sha256")
    parser.add_argument("--expected-evidence-sha256")
    parser.add_argument("--provenance-policy", type=Path)
    parser.add_argument("--app-name")
    parser.add_argument("--app-version")
    parser.add_argument("--build-id")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    app_metadata = {
        key: value
        for key, value in {
            "name": args.app_name,
            "version": args.app_version,
            "build_id": args.build_id,
        }.items()
        if value
    }
    manifest = generate_release_provenance_manifest(
        repo_root=args.repo_root,
        run_id=args.run_id,
        evidence_run_id=args.evidence_run_id,
        artifact_path=args.artifact,
        evidence_manifest_path=args.evidence_manifest,
        artifact_scope=args.artifact_scope,
        evidence_scope=args.evidence_scope,
        expected_artifact_sha256=args.expected_artifact_sha256,
        expected_evidence_sha256=args.expected_evidence_sha256,
        app_metadata=app_metadata,
        provenance_policy_path=args.provenance_policy,
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0 if manifest["verdict"] == "go" else 2


if __name__ == "__main__":
    raise SystemExit(main())
