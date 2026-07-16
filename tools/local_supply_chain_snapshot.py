#!/usr/bin/env python3
"""Capture local model and FFmpeg facts without network or secret access.

This is an evidence collector, not a release approval tool.  A local cache
snapshot can prove which bytes were observed on this machine, but it cannot
prove upstream licensing, redistribution permission, or an immutable model
revision.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from typing import Any, Iterable


MODEL_IDS = (
    "iic/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
    "iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online",
    "iic/speech_fsmn_vad_zh-cn-16k-common-pytorch",
    "iic/punc_ct-transformer_cn-en-common-vocab471067-large",
)
SCHEMA_VERSION = "meeting_copilot.local_supply_chain_snapshot.v1"


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _regular_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            yield path


def _read_revision_metadata(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": path.name,
        "exists": path.is_file(),
        "sha256": sha256_file(path) if path.is_file() else None,
        "revision": None,
        "revision_status": "missing",
    }
    if not path.is_file():
        return result
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        result["revision_status"] = "unreadable"
        return result
    for line in text.splitlines():
        if line.lower().startswith("revision:"):
            revision = line.split(":", 1)[1].split(",", 1)[0].strip()
            result["revision"] = revision or None
            result["revision_status"] = (
                "immutable_candidate"
                if revision and revision.lower() not in {"master", "main", "latest"}
                else "mutable_or_unresolved"
            )
            break
    return result


def model_snapshot(model_root: Path, model_id: str) -> dict[str, Any]:
    model_dir = model_root / model_id.split("/", 1)[-1]
    files: list[dict[str, Any]] = []
    if model_dir.is_dir():
        for path in _regular_files(model_dir):
            relative = path.relative_to(model_dir).as_posix()
            files.append(
                {
                    "path": relative,
                    "size_bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )
    canonical = json.dumps(files, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    directory_manifest_sha256 = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    mv = _read_revision_metadata(model_dir / ".mv")
    msc = _read_revision_metadata(model_dir / ".msc")
    return {
        "model_id": model_id,
        "local_path_kind": "modelscope_cache_model_id_suffix",
        "present": model_dir.is_dir(),
        "file_count": len(files),
        "files": files,
        "directory_manifest_sha256": directory_manifest_sha256 if files else None,
        "modelscope_metadata": {"mv": mv, "msc": msc},
        "immutable_revision": (
            mv["revision"]
            if mv["revision_status"] == "immutable_candidate"
            else None
        ),
        "redistribution_status": "unresolved",
        "note": "Local bytes observed; upstream license and redistribution approval not inferred.",
    }


def _run_capture(argv: list[str], *, timeout: float = 10.0) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "unavailable", "error_type": type(exc).__name__}
    return {
        "status": "captured" if completed.returncode == 0 else "failed",
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def ffmpeg_snapshot(ffmpeg_path: Path | None = None) -> dict[str, Any]:
    resolved = ffmpeg_path or (Path(shutil.which("ffmpeg")) if shutil.which("ffmpeg") else None)
    if resolved is None or not resolved.is_file():
        return {
            "present": False,
            "path_kind": "not_found",
            "sha256": None,
            "version": {"status": "unavailable"},
            "buildconf": {"status": "unavailable"},
            "license": {"status": "unavailable"},
            "immutable_revision": None,
            "redistribution_status": "unresolved",
        }
    return {
        "present": True,
        "path_kind": "local_executable",
        "basename": resolved.name,
        "sha256": sha256_file(resolved),
        "version": _run_capture([str(resolved), "-version"]),
        "buildconf": _run_capture([str(resolved), "-buildconf"]),
        "license": _run_capture([str(resolved), "-L"]),
        "immutable_revision": None,
        "redistribution_status": "unresolved",
        "note": "Local executable observed; bundled binary provenance and redistribution approval not inferred.",
    }


def build_snapshot(*, model_root: Path, ffmpeg_path: Path | None = None) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "collection_policy": {
            "network_called": False,
            "secrets_read": False,
            "configs_local_read": False,
            "model_bytes_downloaded": False,
            "release_approval_granted": False,
        },
        "model_root_kind": "caller_supplied_local_cache_root",
        "models": [model_snapshot(model_root, model_id) for model_id in MODEL_IDS],
        "binaries": [{"id": "ffmpeg/desktop-bundle", **ffmpeg_snapshot(ffmpeg_path)}],
        "release_policy": "This snapshot is evidence only; unresolved licensing/revision fields remain unresolved.",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--model-root",
        type=Path,
        default=Path.home() / ".cache/modelscope/hub/models/iic",
    )
    parser.add_argument("--ffmpeg-path", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    snapshot = build_snapshot(model_root=args.model_root, ffmpeg_path=args.ffmpeg_path)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(snapshot, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": "captured", "output": str(args.output), "model_count": len(snapshot["models"])}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
