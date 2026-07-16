import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "generate_sbom_inventory.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("generate_sbom_inventory", TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_repository_sbom_is_deterministic_and_contains_all_source_families(tmp_path):
    tool = _load_tool()
    first = tool.build_sbom(REPO_ROOT)
    second = tool.build_sbom(REPO_ROOT)

    assert first == second
    assert first["bomFormat"] == "CycloneDX"
    assert first["specVersion"] == "1.5"
    names = {component["name"] for component in first["components"]}
    assert "fastapi" in names
    assert "react" in names
    assert "tauri" in names
    assert "iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online" in names
    assert all(component["bom-ref"] for component in first["components"])


def test_repository_sbom_preserves_unresolved_supply_chain_status(tmp_path):
    tool = _load_tool()
    payload = tool.build_sbom(REPO_ROOT)

    model = next(
        component
        for component in payload["components"]
        if component["name"].startswith("iic/speech_paraformer")
    )
    properties = {item["name"]: item["value"] for item in model["properties"]}
    assert properties["immutable_revision"] == "unresolved"
    assert properties["redistribution_status"] == "unresolved"
    assert properties["license_status"] == "unresolved"


def test_write_sbom_uses_stable_json_and_does_not_need_network(tmp_path):
    tool = _load_tool()
    output = tmp_path / "sbom.cdx.json"

    payload = tool.write_sbom(REPO_ROOT, output)

    assert output.is_file()
    assert json.loads(output.read_text(encoding="utf-8")) == payload
    assert "secret" not in output.read_text(encoding="utf-8").lower()
