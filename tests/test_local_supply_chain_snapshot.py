import hashlib
import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "local_supply_chain_snapshot.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("local_supply_chain_snapshot", TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_model_snapshot_hashes_all_local_files_and_does_not_infer_revision(tmp_path):
    tool = load_tool()
    root = tmp_path / "iic"
    model = root / "example-model"
    model.mkdir(parents=True)
    (model / "model.pt").write_bytes(b"model")
    (model / "nested").mkdir()
    (model / "nested" / "config.json").write_text('{"ok":true}\n', encoding="utf-8")
    (model / ".mv").write_text("Revision:master,CreatedAt:123\n", encoding="utf-8")
    (model / ".msc").write_text("metadata\n", encoding="utf-8")

    snapshot = tool.model_snapshot(root, "iic/example-model")

    assert snapshot["present"] is True
    assert snapshot["file_count"] == 4
    assert snapshot["immutable_revision"] is None
    assert snapshot["modelscope_metadata"]["mv"]["revision_status"] == "mutable_or_unresolved"
    assert snapshot["redistribution_status"] == "unresolved"
    assert {entry["path"] for entry in snapshot["files"]} == {
        ".msc",
        ".mv",
        "model.pt",
        "nested/config.json",
    }
    model_hash = next(entry["sha256"] for entry in snapshot["files"] if entry["path"] == "model.pt")
    assert model_hash == hashlib.sha256(b"model").hexdigest()
    assert len(snapshot["directory_manifest_sha256"]) == 64


def test_missing_model_is_explicit_and_deterministic(tmp_path):
    tool = load_tool()
    first = tool.model_snapshot(tmp_path, "iic/missing")
    second = tool.model_snapshot(tmp_path, "iic/missing")
    assert first == second
    assert first["present"] is False
    assert first["file_count"] == 0
    assert first["directory_manifest_sha256"] is None


def test_ffmpeg_snapshot_never_marks_local_binary_releasable(tmp_path):
    tool = load_tool()
    fake = tmp_path / "ffmpeg"
    fake.write_bytes(b"fake executable")
    snapshot = tool.ffmpeg_snapshot(fake)
    assert snapshot["present"] is True
    assert snapshot["sha256"] == hashlib.sha256(b"fake executable").hexdigest()
    assert snapshot["immutable_revision"] is None
    assert snapshot["redistribution_status"] == "unresolved"


def test_cli_writes_json_without_reading_local_config(tmp_path, monkeypatch, capsys):
    tool = load_tool()
    output = tmp_path / "snapshot.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "local_supply_chain_snapshot.py",
            "--model-root",
            str(tmp_path / "models"),
            "--output",
            str(output),
        ],
    )
    assert tool.main() == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["collection_policy"]["network_called"] is False
    assert payload["collection_policy"]["secrets_read"] is False
    assert payload["release_policy"].startswith("This snapshot is evidence only")
    assert json.loads(capsys.readouterr().out)["status"] == "captured"
