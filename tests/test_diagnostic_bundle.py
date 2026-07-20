import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
from zipfile import ZipFile


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "code" / "web_mvp" / "backend"
TOOL_PATH = REPO_ROOT / "tools" / "generate_diagnostic_bundle.py"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from meeting_copilot_web_mvp import diagnostic_bundle  # noqa: E402


def load_tool_module():
    spec = importlib.util.spec_from_file_location("generate_diagnostic_bundle", TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _read_bundle(path: Path) -> tuple[dict, dict, bytes]:
    with ZipFile(path) as archive:
        assert archive.namelist() == ["diagnostics.json", "manifest.json"]
        diagnostics_bytes = archive.read("diagnostics.json")
        diagnostics = json.loads(diagnostics_bytes)
        manifest = json.loads(archive.read("manifest.json"))
    return diagnostics, manifest, diagnostics_bytes


def _representative_snapshot() -> dict:
    return {
        "version": {
            "app_version": "0.1.0",
            "build_number": 17,
            "commit": "abc1234",
            "ignored_version_detail": "not exported",
        },
        "config_summary": {
            "app_mode": "desktop",
            "locale": "zh-CN",
            "recording_enabled": True,
            "feature_flags": {"realtime_cards": True, "private_note": "not-a-boolean"},
        },
        "provider_capabilities": {
            "llm": {
                "provider": "openai-compatible",
                "mode": "remote",
                "configured": True,
                "supports_streaming": True,
                "features": ["chat", "json_schema"],
            },
            "asr": {
                "provider": "funasr",
                "available": True,
                "supports_realtime": True,
            },
        },
        "stage_metrics": {
            "asr": {
                "realtime_factor": 0.42,
                "p95_latency_ms": 820.5,
                "queue_depth": 2,
                "dropped_frames": 3,
                "recording_gap_count": 1,
            },
            "llm": {
                "ttft_ms": 640,
                "p95_latency_ms": 1800,
                "max_queue_depth": 4,
            },
        },
        "errors": [
            {
                "stage": "llm",
                "category": "provider_timeout",
                "code": "upstream_timeout",
                "error_type": "TimeoutError",
                "retryable": True,
                "count": 2,
                "message": "freeform message is intentionally excluded",
            }
        ],
    }


def test_report_contains_only_allowlisted_diagnostic_aggregates():
    report = diagnostic_bundle.build_diagnostic_report(_representative_snapshot())

    assert report["version"] == {
        "app_version": "0.1.0",
        "build_number": 17,
        "commit": "abc1234",
    }
    assert report["config_summary"] == {
        "app_mode": "desktop",
        "feature_flags": {"realtime_cards": True},
        "locale": "zh-CN",
        "recording_enabled": True,
    }
    assert {item["kind"] for item in report["provider_capabilities"]} == {"asr", "llm"}
    assert report["stage_metrics"] == [
        {
            "metrics": {
                "dropped_frames": 3,
                "p95_latency_ms": 820.5,
                "queue_depth": 2,
                "realtime_factor": 0.42,
                "recording_gap_count": 1,
            },
            "stage": "asr",
        },
        {
            "metrics": {"max_queue_depth": 4, "p95_latency_ms": 1800, "ttft_ms": 640},
            "stage": "llm",
        },
    ]
    assert report["errors"] == [
        {
            "category": "provider_timeout",
            "code": "upstream_timeout",
            "count": 2,
            "error_type": "TimeoutError",
            "retryable": True,
            "stage": "llm",
        }
    ]


def test_deliberate_secret_transcript_audio_database_and_path_injection_never_leaks(tmp_path):
    secret = "sk-next007-ultra-private-key"
    authorization = "Bearer next007-authorization-secret"
    transcript = "NEXT007_PRIVATE_TRANSCRIPT_客户明天签约_不可泄露"
    audio_marker = "NEXT007_PRIVATE_AUDIO_BYTES"
    database_marker = "NEXT007_PRIVATE_SQLITE_DATABASE"
    private_path = "/Users/private-person/Library/Application Support/MeetingCopilot/private.db"
    snapshot = _representative_snapshot()
    snapshot.update(
        {
            "api_key": secret,
            "Authorization": authorization,
            "transcript": transcript,
            "audio": audio_marker.encode("utf-8"),
            "database": database_marker.encode("utf-8"),
            "absolute_user_path": private_path,
        }
    )
    snapshot["config_summary"].update(
        {
            "api_key": secret,
            "authorization": authorization,
            "transcript": transcript,
            "audio_bytes": audio_marker,
            "database_path": private_path,
            "data_dir": private_path,
            "release_channel": transcript,
        }
    )
    snapshot["provider_capabilities"]["llm"].update(
        {
            "raw_prompt": transcript,
            "raw_response": transcript,
            "authorization": authorization,
            "status": "next007-authorization-secret",
        }
    )
    snapshot["stage_metrics"]["asr"].update(
        {"transcript": transcript, "audio_bytes": audio_marker.encode("utf-8")}
    )
    snapshot["errors"][0].update(
        {
            "message": f"{authorization}; key={secret}; transcript={transcript}; path={private_path}",
            "traceback": f"database={database_marker}",
            "request_audio": audio_marker.encode("utf-8"),
            "code": transcript,
        }
    )
    snapshot["version"]["build"] = secret

    output = tmp_path / "diagnostics.zip"
    diagnostic_bundle.create_diagnostic_bundle(snapshot, output)
    archive_bytes = output.read_bytes()
    diagnostics, manifest, _ = _read_bundle(output)
    serialized = archive_bytes + json.dumps(
        {"diagnostics": diagnostics, "manifest": manifest}, ensure_ascii=False
    ).encode("utf-8")

    for forbidden in (
        secret,
        authorization,
        "next007-authorization-secret",
        transcript,
        audio_marker,
        database_marker,
        private_path,
        "/Users/private-person",
    ):
        assert forbidden.encode("utf-8") not in serialized
    assert diagnostics["sanitization"] == {
        "binary_values_included": False,
        "freeform_error_text_included": False,
        "private_paths_included": False,
        "strategy": "strict_allowlist",
    }
    assert manifest["privacy"] == {
        "binary_payloads_included": False,
        "database_contents_included": False,
        "freeform_meeting_content_included": False,
        "private_paths_included": False,
        "secret_values_included": False,
    }


def test_bundle_manifest_and_package_sha256_are_deterministic(tmp_path):
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"

    first_result = diagnostic_bundle.create_diagnostic_bundle(_representative_snapshot(), first)
    second_result = diagnostic_bundle.create_diagnostic_bundle(_representative_snapshot(), second)

    assert first.read_bytes() == second.read_bytes()
    expected_sha256 = hashlib.sha256(first.read_bytes()).hexdigest()
    assert first_result["bundle_sha256"] == second_result["bundle_sha256"] == expected_sha256
    assert first.with_suffix(".zip.sha256").read_text(encoding="ascii") == f"{expected_sha256}  first.zip\n"
    assert second.with_suffix(".zip.sha256").read_text(encoding="ascii") == f"{expected_sha256}  second.zip\n"

    _, manifest, diagnostics_bytes = _read_bundle(first)
    assert manifest["entries"] == [
        {
            "name": "diagnostics.json",
            "sha256": hashlib.sha256(diagnostics_bytes).hexdigest(),
            "size_bytes": len(diagnostics_bytes),
        }
    ]


def test_cli_generates_bundle_without_echoing_absolute_paths(tmp_path):
    tool = load_tool_module()
    input_path = tmp_path / "snapshot.json"
    output_path = tmp_path / "support" / "meeting-copilot-diagnostics.zip"
    input_path.write_text(json.dumps(_representative_snapshot()), encoding="utf-8")
    stdout = io.StringIO()

    exit_code = tool.main(
        ["--input", str(input_path), "--output", str(output_path)],
        out=stdout,
    )

    result = json.loads(stdout.getvalue())
    assert exit_code == 0
    assert result["bundle"] == output_path.name
    assert result["checksum"] == output_path.name + ".sha256"
    assert str(tmp_path) not in stdout.getvalue()
    assert output_path.is_file()
    assert output_path.with_suffix(".zip.sha256").is_file()
