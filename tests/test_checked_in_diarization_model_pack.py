from __future__ import annotations

import json
from pathlib import Path
import re


REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = (
    REPO_ROOT
    / "code/asr_runtime/model_packs/diarization-zh-cn-vad-v2.0.4-camplus-v1.0.0.manifest.json"
)
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")


def test_checked_in_diarization_pack_is_local_only_and_public_release_fail_closed() -> None:
    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    assert payload["schema_version"] == (
        "meeting_copilot.controlled_diarization_model_pack.v1"
    )
    assert payload["pack_id"] == "diarization-zh-cn"
    assert payload["offline_boundary"] == {
        "requires_network": False,
        "runtime_downloads_allowed": False,
        "remote_asr_used": False,
    }
    assert payload["verification"] == {
        "status": "verified_for_internal_packaging",
        "counts_as_public_release": False,
    }
    assert payload["redistribution"]["status"] == (
        "public_redistribution_unresolved"
    )
    assert payload["redistribution"]["public_redistribution_approved"] is False
    assert set(payload["models"]) == {"vad", "camplus"}
    assert SHA256_PATTERN.fullmatch(payload["sha256"])


def test_checked_in_diarization_pack_has_immutable_allowlisted_provenance_only() -> None:
    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    for model in payload["models"].values():
        provenance = model["provenance"]
        assert model["version"] == provenance["immutable_revision"]
        assert model["version"].casefold() not in {"main", "master", "latest"}
        assert provenance["source_url"].startswith("https://modelscope.cn/models/")
        assert provenance["public_redistribution_approved"] is False
        assert SHA256_PATTERN.fullmatch(model["sha256"])
        assert model["files"]
        assert all(not Path(relative).is_absolute() for relative in model["files"])
        assert all(SHA256_PATTERN.fullmatch(digest) for digest in model["files"].values())

    serialized = json.dumps(payload, ensure_ascii=True, sort_keys=True).casefold()
    for forbidden in (
        "/users/",
        ".cache/",
        "source_directory",
        "api_key",
        "authorization",
        "credential",
        "password",
        "secret",
    ):
        assert forbidden not in serialized
