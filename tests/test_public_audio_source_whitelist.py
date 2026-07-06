import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = REPO_ROOT / "data" / "asr_eval" / "public_sources.json"
TOOL_PATH = REPO_ROOT / "tools" / "public_audio_source_whitelist.py"


def load_tool_module():
    spec = importlib.util.spec_from_file_location(
        "public_audio_source_whitelist",
        TOOL_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_public_audio_source_whitelist_exists_and_disables_downloads():
    sources = json.loads(SOURCE_PATH.read_text(encoding="utf-8"))

    assert sources["manifest_version"] == "public_audio_source_whitelist.v1"
    assert sources["default_download_status"] == "not_started"
    assert sources["safe_to_download_now"] is False
    assert sources["safe_to_read_user_audio"] is False
    assert sources["safe_to_read_configs_local"] is False
    assert sources["allowed_storage_roots"] == [
        "data/asr_eval/public_raw",
        "artifacts/tmp/public_audio",
    ]
    assert sources["forbidden_storage_roots"] == [
        "configs/local",
        "data/asr_eval/local_samples",
        "data/asr_eval/samples",
        "data/local_runtime",
        "outputs",
    ]

    source_by_id = {source["source_id"]: source for source in sources["sources"]}
    assert set(source_by_id) == {
        "aishell4_openslr_slr111",
        "alimeeting_openslr_slr119",
        "aishell1_openslr_slr33",
    }
    assert source_by_id["aishell4_openslr_slr111"]["license"] == "CC BY-SA 4.0"
    assert source_by_id["alimeeting_openslr_slr119"]["license"] == "CC BY-SA 4.0"
    assert source_by_id["aishell1_openslr_slr33"]["license"] == "Apache License v2.0"
    for source in sources["sources"]:
        assert source["default_download_enabled"] is False
        assert source["raw_audio_committed_to_repo"] is False
        assert source["product_value_validation_allowed"] is False


def test_observed_public_audio_candidates_are_not_whitelisted_for_download():
    sources = json.loads(SOURCE_PATH.read_text(encoding="utf-8"))
    source_ids = {source["source_id"] for source in sources["sources"]}
    observed = sources["observed_but_not_whitelisted_sources"]

    assert observed
    observed_ids = {candidate["source_id"] for candidate in observed}
    assert {
        "magichub_web_meeting_candidate",
        "magichub_ramc_conversational_candidate",
        "common_voice_zh_cn_candidate",
        "wenetspeech_excluded_platform_audio",
    }.issubset(observed_ids)
    for candidate in observed:
        assert candidate["source_id"] not in source_ids
        assert candidate["default_download_enabled"] is False
        assert candidate["raw_audio_committed_to_repo"] is False
        assert candidate["product_value_validation_allowed"] is False
        reason = candidate["reason_not_whitelisted"]
        assert "automatic download" in reason or "automatic evaluation" in reason
        assert "product-value gates" in reason


def test_public_audio_whitelist_report_is_source_only_and_path_safe():
    tool = load_tool_module()

    report = tool.build_public_audio_source_whitelist_report(SOURCE_PATH)

    assert report["report_mode"] == "public_audio_source_whitelist_only"
    assert report["download_status"] == "not_started"
    assert report["source_validation_status"] == "passed"
    assert report["source_count"] == 3
    assert report["safe_to_download_now"] is False
    assert report["safe_to_read_user_audio"] is False
    assert report["safe_to_read_configs_local"] is False
    assert report["observed_but_not_whitelisted_count"] >= 4
    observed_ids = {candidate["source_id"] for candidate in report["observed_but_not_whitelisted_sources"]}
    assert {
        "magichub_web_meeting_candidate",
        "magichub_ramc_conversational_candidate",
        "common_voice_zh_cn_candidate",
        "wenetspeech_excluded_platform_audio",
    }.issubset(observed_ids)
    for candidate in report["observed_but_not_whitelisted_sources"]:
        assert candidate["download_status"] == "not_started"
    assert report["next_action"] == "create_bounded_sample_extraction_plan"
    report_json = json.dumps(report, ensure_ascii=False)
    assert "configs/local" in report_json
    assert "data/asr_eval/local_samples" in report_json
    assert "data/asr_eval/samples" in report_json
    assert "/Users/" not in report_json
    assert "private_audio_marker" not in report_json


def test_public_audio_whitelist_rejects_enabled_downloads(tmp_path):
    payload = json.loads(SOURCE_PATH.read_text(encoding="utf-8"))
    payload["sources"][0]["default_download_enabled"] = True
    tool = load_tool_module()

    errors = tool.validate_public_sources(payload)

    assert "source aishell4_openslr_slr111 default_download_enabled must be false" in errors


def test_public_audio_whitelist_rejects_forbidden_source_paths_before_read(monkeypatch):
    tool = load_tool_module()

    def fail_if_read(*args, **kwargs):
        raise AssertionError("source file was read before path guard")

    monkeypatch.setattr(Path, "read_text", fail_if_read)

    report = tool.build_public_audio_source_whitelist_report(Path("configs/local/public_sources.json"))

    assert report["source_validation_status"] == "failed"
    assert report["source_validation_errors"] == ["source_path is forbidden"]
    assert report["sources"] == []
    assert report["safe_to_download_now"] is False
    assert report["safe_to_read_configs_local"] is False


def test_public_audio_whitelist_rejects_repo_outside_and_symlink_source_paths_before_read(
    monkeypatch,
    tmp_path,
):
    tool = load_tool_module()
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    outside = tmp_path / "outside-public-sources.json"
    outside.write_text("{}", encoding="utf-8")
    allowed_dir = repo_root / "artifacts" / "tmp" / "public_audio"
    allowed_dir.mkdir(parents=True)
    symlink_path = allowed_dir / "sources-link.json"
    symlink_path.symlink_to(outside)
    monkeypatch.setattr(tool, "REPO_ROOT", repo_root)

    def fail_if_read(*args, **kwargs):
        raise AssertionError("source file was read before path guard")

    monkeypatch.setattr(Path, "read_text", fail_if_read)

    outside_report = tool.build_public_audio_source_whitelist_report(outside)
    symlink_report = tool.build_public_audio_source_whitelist_report(symlink_path)

    assert outside_report["source_validation_status"] == "failed"
    assert outside_report["source_validation_errors"] == ["source_path is outside repository"]
    assert symlink_report["source_validation_status"] == "failed"
    assert symlink_report["source_validation_errors"] == ["source_path resolves outside repository"]
