import importlib.util
import io
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "local_artifact_retention.py"


def load_tool_module():
    spec = importlib.util.spec_from_file_location(
        "local_artifact_retention",
        TOOL_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_retention_report_keeps_approved_artifacts_without_reading_contents(tmp_path):
    tool = load_tool_module()
    repo_root = tmp_path / "repo"
    audio_path = repo_root / "artifacts/tmp/audio_health/session-a.mainline-health.wav"
    report_path = repo_root / "artifacts/tmp/mainline_selftests/session-a.mainline-usable-e2e.json"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(b"private-audio-bytes-should-not-be-read")
    report_path.write_text("private-report-contents-should-not-be-read", encoding="utf-8")

    report = tool.build_local_artifact_retention_report(
        session_id="session-a",
        artifact_paths=[audio_path, report_path],
        repo_root=repo_root,
        delete=False,
    )

    assert report["report_mode"] == "local_artifact_retention"
    assert report["retention_status"] == "local_artifacts_retained"
    assert report["delete_requested"] is False
    assert report["retained_artifact_count"] == 2
    assert report["deleted_artifact_count"] == 0
    assert report["blocked_artifact_count"] == 0
    assert {
        item["path"] for item in report["artifacts"]
    } == {
        "artifacts/tmp/audio_health/session-a.mainline-health.wav",
        "artifacts/tmp/mainline_selftests/session-a.mainline-usable-e2e.json",
    }
    serialized = json.dumps(report, ensure_ascii=False)
    assert "private-audio-bytes" not in serialized
    assert "private-report-contents" not in serialized
    assert report["privacy_cost_flags"] == {
        "raw_audio_uploaded": False,
        "remote_asr_called": False,
        "llm_called": False,
        "configs_local_read": False,
        "private_user_audio_read": False,
        "paid_provider_used": False,
    }


def test_delete_removes_only_approved_artifacts(tmp_path):
    tool = load_tool_module()
    repo_root = tmp_path / "repo"
    audio_path = repo_root / "artifacts/tmp/audio_health/session-b.system-audio-health.wav"
    report_path = repo_root / "artifacts/tmp/mainline_selftests/session-b.mainline-usable-e2e.md"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(b"audio")
    report_path.write_text("report", encoding="utf-8")

    report = tool.build_local_artifact_retention_report(
        session_id="session-b",
        artifact_paths=[audio_path, report_path],
        repo_root=repo_root,
        delete=True,
    )

    assert report["retention_status"] == "approved_artifacts_deleted"
    assert report["deleted_artifact_count"] == 2
    assert report["retained_artifact_count"] == 0
    assert not audio_path.exists()
    assert not report_path.exists()
    assert all(item["action"] == "deleted" for item in report["artifacts"])


def test_path_guard_blocks_forbidden_and_outside_repo_paths_before_delete(tmp_path):
    tool = load_tool_module()
    repo_root = tmp_path / "repo"
    forbidden_path = repo_root / "configs/local/private.wav"
    outside_path = tmp_path / "outside.wav"
    forbidden_path.parent.mkdir(parents=True, exist_ok=True)
    outside_path.write_bytes(b"outside")

    report = tool.build_local_artifact_retention_report(
        session_id="session-c",
        artifact_paths=[forbidden_path, outside_path],
        repo_root=repo_root,
        delete=True,
    )

    assert report["retention_status"] == "blocked_by_artifact_path_guard"
    assert report["blocked_artifact_count"] == 2
    assert report["deleted_artifact_count"] == 0
    assert outside_path.exists()
    assert all(item["path"] == "<redacted_invalid_path>" for item in report["artifacts"])


def test_cli_writes_retention_json(tmp_path):
    tool = load_tool_module()
    repo_root = tmp_path / "repo"
    audio_path = repo_root / "artifacts/tmp/audio_health/session-d.mainline-health.wav"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(b"audio")
    out = io.StringIO()

    exit_code = tool.main(
        [
            "--repo-root",
            str(repo_root),
            "--session-id",
            "session-d",
            "--artifact-path",
            str(audio_path),
        ],
        out=out,
    )

    payload = json.loads(out.getvalue())
    assert exit_code == 0
    assert payload["retention_status"] == "local_artifacts_retained"
    assert payload["artifacts"][0]["path"] == "artifacts/tmp/audio_health/session-d.mainline-health.wav"
