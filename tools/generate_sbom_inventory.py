#!/usr/bin/env python3
"""Generate a deterministic CycloneDX inventory from repository lockfiles.

This is an inventory generator, not a license approval tool.  Missing license,
artifact, model, or binary provenance is retained as ``unresolved`` so the
release gate can remain fail-closed.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import tomllib
from typing import Any, Iterable
from urllib.parse import quote
from uuid import UUID, uuid5


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO_ROOT / "sbom.cdx.json"
SBOM_SCHEMA_VERSION = "1.5"
SBOM_NAMESPACE = UUID("3d4b6f4c-3f1a-5c78-9c6f-5e6dcfc12c13")
SHA256_RE = re.compile(r"^(?:sha256[:=])?([0-9a-fA-F]{64})$")
REQUIREMENT_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)==([^;\s]+)")


def build_sbom(repo_root: Path) -> dict[str, Any]:
    """Build a deterministic SBOM without network access or secret reads."""

    root = repo_root.resolve()
    components: list[dict[str, Any]] = []
    components.extend(_python_lock_components(root / "code/web_mvp/backend"))
    components.extend(_requirements_components(root / "code/asr_runtime"))
    components.extend(_npm_components(root / "code/web_mvp/frontend_v2/package-lock.json"))
    components.extend(_cargo_components(root / "code/desktop_tauri/src-tauri/Cargo.lock"))
    components.extend(_provenance_components(root / "configs/release-provenance.json"))
    normalized = _deduplicate_components(components)
    component_refs = [component["bom-ref"] for component in normalized]
    serial = "urn:uuid:" + str(uuid5(SBOM_NAMESPACE, "\n".join(component_refs)))
    return {
        "bomFormat": "CycloneDX",
        "specVersion": SBOM_SCHEMA_VERSION,
        "serialNumber": serial,
        "version": 1,
        "metadata": {
            "component": {
                "type": "application",
                "name": "meeting-copilot",
                "version": "0.1.0",
                "bom-ref": "application:meeting-copilot@0.1.0",
            },
            "properties": [
                {"name": "inventory.network_access", "value": "false"},
                {"name": "inventory.license_review", "value": "unresolved_until_reviewed"},
            ],
        },
        "components": normalized,
    }


def write_sbom(repo_root: Path, output_path: Path) -> dict[str, Any]:
    payload = build_sbom(repo_root)
    output = output_path if output_path.is_absolute() else repo_root / output_path
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def _python_lock_components(backend_root: Path) -> list[dict[str, Any]]:
    lock_path = backend_root / "uv.lock"
    pyproject_path = backend_root / "pyproject.toml"
    if not lock_path.is_file():
        raise FileNotFoundError(lock_path)
    if not pyproject_path.is_file():
        raise FileNotFoundError(pyproject_path)
    lock = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    project = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    direct = {
        _normalize_package_name(name)
        for raw in [
            *(project.get("project", {}).get("dependencies", []) or []),
            *(
                project.get("dependency-groups", {}).get("dev", [])
                or project.get("tool", {}).get("uv", {}).get("dev-dependencies", [])
                or []
            ),
        ]
        for name in [_requirement_name(str(raw))]
        if name
    }
    components: list[dict[str, Any]] = []
    for package in lock.get("package", []) or []:
        if not isinstance(package, dict):
            continue
        name = str(package.get("name") or "").strip()
        version = str(package.get("version") or "").strip()
        if not name or not version:
            continue
        hashes = _uv_hashes(package)
        source = package.get("source") if isinstance(package.get("source"), dict) else {}
        resolved = str(source.get("url") or "").strip() or None
        components.append(
            _component(
                name=name,
                version=version,
                purl=f"pkg:pypi/{quote(_normalize_package_name(name), safe='._-')}@{quote(version, safe='._+-')}",
                scope="required" if _normalize_package_name(name) in direct else "optional",
                hashes=hashes,
                properties={
                    "source": "uv.lock",
                    "resolved": resolved or "unresolved",
                    "license_status": "unresolved",
                },
            )
        )
    return components


def _requirements_components(asr_root: Path) -> list[dict[str, Any]]:
    components: list[dict[str, Any]] = []
    for path in sorted(asr_root.glob("requirements-*.lock")):
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            match = REQUIREMENT_RE.match(line)
            if match is None:
                continue
            name, version = match.groups()
            components.append(
                _component(
                    name=name,
                    version=version,
                    purl=f"pkg:pypi/{quote(_normalize_package_name(name), safe='._-')}@{quote(version, safe='._+-')}",
                    scope="required",
                    properties={
                        "source": path.relative_to(asr_root.parent.parent).as_posix(),
                        "artifact_hash_status": "unresolved",
                        "license_status": "unresolved",
                    },
                )
            )
    return components


def _npm_components(lock_path: Path) -> list[dict[str, Any]]:
    if not lock_path.is_file():
        raise FileNotFoundError(lock_path)
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    packages = lock.get("packages", {}) if isinstance(lock, dict) else {}
    components: list[dict[str, Any]] = []
    for package_path, package in sorted(packages.items()):
        if not package_path or package_path == "" or not isinstance(package, dict):
            continue
        name = _npm_package_name(package_path)
        version = str(package.get("version") or "").strip()
        if not name or not version:
            continue
        properties = {
            "source": "package-lock.json",
            "resolved": str(package.get("resolved") or "unresolved"),
            "license_status": str(package.get("license") or "unresolved"),
        }
        integrity = str(package.get("integrity") or "").strip()
        hashes = _integrity_hashes(integrity)
        components.append(
            _component(
                name=name,
                version=version,
                purl=f"pkg:npm/{quote(name, safe='@/_-.')}@{quote(version, safe='._+-')}",
                scope="required" if package_path.count("node_modules/") == 1 else "optional",
                hashes=hashes,
                properties=properties,
            )
        )
    return components


def _cargo_components(lock_path: Path) -> list[dict[str, Any]]:
    if not lock_path.is_file():
        raise FileNotFoundError(lock_path)
    lock = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    components: list[dict[str, Any]] = []
    for package in lock.get("package", []) or []:
        if not isinstance(package, dict):
            continue
        name = str(package.get("name") or "").strip()
        version = str(package.get("version") or "").strip()
        if not name or not version:
            continue
        source = str(package.get("source") or "unresolved")
        checksum = _normalize_hash(package.get("checksum"))
        components.append(
            _component(
                name=name,
                version=version,
                purl=f"pkg:cargo/{quote(name, safe='_-')}@{quote(version, safe='._+-')}",
                scope="required",
                hashes=[checksum] if checksum else [],
                properties={
                    "source": "Cargo.lock",
                    "resolved": source,
                    "license_status": "unresolved",
                },
            )
        )
    return components


def _provenance_components(policy_path: Path) -> list[dict[str, Any]]:
    if not policy_path.is_file():
        raise FileNotFoundError(policy_path)
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    components: list[dict[str, Any]] = []
    for item in [*(policy.get("models", []) or []), *(policy.get("binaries", []) or [])]:
        if not isinstance(item, dict):
            continue
        component_id = str(item.get("id") or "").strip()
        if not component_id:
            continue
        component_type = "file" if component_id.startswith("ffmpeg/") else "machine-learning-model"
        properties = {
            "source": "configs/release-provenance.json",
            "immutable_revision": str(item.get("immutable_revision") or "unresolved"),
            "redistribution_status": str(item.get("redistribution_status") or "unresolved"),
            "license_status": "unresolved",
        }
        artifact_hash = _normalize_hash(item.get("artifact_manifest_sha256") or item.get("artifact_sha256"))
        components.append(
            _component(
                name=component_id,
                version=str(item.get("immutable_revision") or "unresolved"),
                purl=None,
                scope="required",
                hashes=[artifact_hash] if artifact_hash else [],
                component_type=component_type,
                properties=properties,
            )
        )
    return components


def _component(
    *,
    name: str,
    version: str,
    purl: str | None,
    scope: str,
    hashes: Iterable[str] = (),
    component_type: str = "library",
    properties: dict[str, str] | None = None,
) -> dict[str, Any]:
    bom_ref = f"{component_type}:{name}@{version}"
    component: dict[str, Any] = {
        "type": component_type,
        "bom-ref": bom_ref,
        "name": name,
        "version": version,
        "scope": scope,
        "properties": [
            {"name": key, "value": value}
            for key, value in sorted((properties or {}).items())
        ],
    }
    if purl:
        component["purl"] = purl
    normalized_hashes = sorted({item.lower() for item in hashes if SHA256_RE.fullmatch(item)})
    if normalized_hashes:
        component["hashes"] = [{"alg": "SHA-256", "content": item} for item in normalized_hashes]
    return component


def _deduplicate_components(components: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for component in components:
        key = component["bom-ref"]
        previous = merged.get(key)
        if previous is None:
            merged[key] = component
            continue
        properties = {
            str(item.get("name")): str(item.get("value"))
            for item in [*(previous.get("properties", []) or []), *(component.get("properties", []) or [])]
            if isinstance(item, dict) and item.get("name")
        }
        previous["properties"] = [
            {"name": name, "value": value}
            for name, value in sorted(properties.items())
        ]
        hashes = {
            str(item.get("content")).lower()
            for item in [*(previous.get("hashes", []) or []), *(component.get("hashes", []) or [])]
            if isinstance(item, dict) and SHA256_RE.fullmatch(str(item.get("content") or ""))
        }
        if hashes:
            previous["hashes"] = [{"alg": "SHA-256", "content": item} for item in sorted(hashes)]
    return [merged[key] for key in sorted(merged)]


def _uv_hashes(package: dict[str, Any]) -> list[str]:
    hashes: list[str] = []
    for key in ("sdist", "wheels"):
        values = package.get(key)
        if isinstance(values, list):
            for item in values:
                if isinstance(item, dict):
                    normalized = _normalize_hash(item.get("hash"))
                    if normalized:
                        hashes.append(normalized)
    return hashes


def _integrity_hashes(integrity: str) -> list[str]:
    if integrity.startswith("sha512-"):
        return []
    normalized = _normalize_hash(integrity)
    return [normalized] if normalized else []


def _normalize_hash(value: Any) -> str | None:
    match = SHA256_RE.fullmatch(str(value or "").strip())
    return match.group(1).lower() if match else None


def _normalize_package_name(value: str) -> str:
    return value.strip().lower().replace("_", "-")


def _requirement_name(value: str) -> str | None:
    match = re.match(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)", value.strip())
    return _normalize_package_name(match.group(1)) if match else None


def _npm_package_name(package_path: str) -> str | None:
    marker = "node_modules/"
    if marker not in package_path:
        return None
    tail = package_path.rsplit(marker, 1)[1]
    parts = tail.split("/node_modules/", 1)[-1].split("/")
    if not parts:
        return None
    if parts[0].startswith("@") and len(parts) >= 2:
        return f"{parts[0]}/{parts[1]}"
    return parts[0]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    root = args.repo_root.resolve()
    output = args.output if args.output.is_absolute() else root / args.output
    write_sbom(root, output)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
