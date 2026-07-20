from __future__ import annotations

from io import BytesIO
import http.client
import json
from pathlib import Path
import sys
from zipfile import ZIP_STORED, ZipFile, ZipInfo


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS = REPO_ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import full_roadmap_packaged_acceptance as acceptance  # noqa: E402


def _docx_bytes(text: str) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_STORED) as archive:
        for name, content in {
            "[Content_Types].xml": "<Types/>",
            "_rels/.rels": "<Relationships/>",
            "word/document.xml": f"<document><body><p>{text}</p></body></document>",
        }.items():
            info = ZipInfo(name)
            info.date_time = (1980, 1, 1, 0, 0, 0)
            archive.writestr(info, content.encode("utf-8"))
    return buffer.getvalue()


def _download_payloads(markers: dict[str, str], title: str) -> dict[str, bytes]:
    marker_text = "\n".join(markers.values())
    export_json = {
        "schema_version": "meeting_copilot.meeting_export.v1",
        "meeting": {"title": title},
        "documents": {
            kind: {
                "user_final": {
                    "modified": True,
                    "content": {"acceptance_marker": marker},
                }
            }
            for kind, marker in markers.items()
        },
    }
    return {
        "markdown": f"# {title}\n\n{marker_text}\n".encode("utf-8"),
        "docx": _docx_bytes(f"{title}\n{marker_text}"),
        "json": json.dumps(export_json, ensure_ascii=False).encode("utf-8"),
    }


def test_required_checks_cover_the_full_packaged_api_acceptance_surface():
    assert set(acceptance.REQUIRED_CHECKS) == {
        "packaged_binary_started",
        "rust_supervisor_backend",
        "authenticated_bootstrap_api",
        "controlled_local_audio",
        "explicit_fake_openai_gateway",
        "named_meeting",
        "realtime_asr_final",
        "ai_correction",
        "ai_intelligence",
        "ai_follow_up",
        "meeting_end",
        "minutes",
        "approach",
        "index",
        "retry_minutes",
        "retry_approach",
        "retry_index",
        "review_minutes_user_final",
        "review_decisions_user_final",
        "review_action_items_user_final",
        "review_risks_user_final",
        "review_transcript_user_final",
        "export_markdown",
        "export_docx",
        "export_json",
        "history_reopen",
        "recording_range",
        "diagnostic_bundle_redaction",
        "delete_derived",
        "delete_recording",
        "delete_transcript",
        "delete_all",
        "app_cleanup",
        "backend_cleanup",
        "worker_cleanup",
        "backend_port_cleanup",
        "fake_gateway_cleanup",
    }


def test_report_is_fail_closed_until_every_required_check_passes():
    recorder = acceptance.AcceptanceRecorder(run_id="unit-run")
    recorder.pass_check("packaged_binary_started", launched=True)

    report = recorder.finalize(duration_seconds=0.1)

    assert report["status"] == "no_go"
    assert report["decision"]["passed"] is False
    assert report["decision"]["counts_as_real_llm_evidence"] is False
    assert report["decision"]["counts_as_public_release_evidence"] is False
    assert "required_check_not_passed:rust_supervisor_backend" in report["blockers"]


def test_report_can_pass_only_when_every_required_check_is_explicitly_passed():
    recorder = acceptance.AcceptanceRecorder(run_id="unit-run")
    for check_name in acceptance.REQUIRED_CHECKS:
        recorder.pass_check(check_name, observed=True)

    report = recorder.finalize(duration_seconds=0.1)

    assert report["status"] == "go"
    assert report["blockers"] == []
    assert report["decision"] == {
        "passed": True,
        "counts_as_packaged_authenticated_api_evidence": True,
        "counts_as_ui_evidence": False,
        "counts_as_real_llm_evidence": False,
        "counts_as_public_release_evidence": False,
    }


def test_report_sanitizer_removes_secrets_bearer_values_and_absolute_paths(tmp_path):
    secret = "unit-acceptance-secret-value"
    payload = {
        "path": str(tmp_path / "private" / "report.json"),
        "nested": {
            "message": (
                f"Bearer {secret} sk-unit-private-value at "
                "/Users/private-person/Library/private.db"
            )
        },
    }

    sanitized = acceptance.sanitize_report(payload, secrets_to_remove={secret})
    serialized = json.dumps(sanitized, ensure_ascii=False)

    assert secret not in serialized
    assert "sk-unit-private-value" not in serialized
    assert "Bearer" not in serialized
    assert "/Users/private-person" not in serialized
    assert str(tmp_path) not in serialized


def test_download_validation_requires_every_user_final_marker_in_all_formats():
    markers = {
        "minutes": "ACCEPTANCE_MINUTES_MARKER",
        "decisions": "ACCEPTANCE_DECISIONS_MARKER",
        "action_items": "ACCEPTANCE_ACTIONS_MARKER",
        "risks": "ACCEPTANCE_RISKS_MARKER",
        "transcript": "ACCEPTANCE_TRANSCRIPT_MARKER",
    }
    payloads = _download_payloads(markers, "Packaged API Acceptance")

    result = acceptance.validate_downloads(
        markdown=payloads["markdown"],
        docx=payloads["docx"],
        json_bytes=payloads["json"],
        markers=markers,
        title="Packaged API Acceptance",
    )

    assert result == {
        "markdown": {"passed": True, "reason": None},
        "docx": {"passed": True, "reason": None},
        "json": {"passed": True, "reason": None},
    }

    broken_docx = _docx_bytes("Packaged API Acceptance\nACCEPTANCE_MINUTES_MARKER")
    broken = acceptance.validate_downloads(
        markdown=payloads["markdown"],
        docx=broken_docx,
        json_bytes=payloads["json"],
        markers=markers,
        title="Packaged API Acceptance",
    )
    assert broken["docx"] == {
        "passed": False,
        "reason": "user_final_marker_missing",
    }


def test_realtime_final_accepts_authenticated_canonical_commit_when_ws_drain_misses_it():
    passed, evidence = acceptance.realtime_final_evidence(
        asr_result={
            "ready": True,
            "non_empty_final_count": 0,
            "rejected": False,
            "transport_error": None,
        },
        segments=[{"segment_id": "segment-1", "transcript_seq": 1}],
        event_types=["transcript.segment.finalized", "meeting.intelligence.applied"],
    )

    assert passed is True
    assert evidence == {
        "provider": "packaged_local_funasr",
        "ready": True,
        "websocket_final_count": 0,
        "canonical_api_final_count": 1,
        "finalized_event_observed": True,
    }

    missing, _ = acceptance.realtime_final_evidence(
        asr_result={"ready": True, "non_empty_final_count": 0},
        segments=[],
        event_types=[],
    )
    assert missing is False


def test_fake_gateway_is_explicit_openai_compatible_and_never_records_credentials():
    credential = "ephemeral-unit-credential"
    provider = acceptance.AcceptanceOpenAIProvider(credential=credential)
    provider.start()
    try:
        connection = http.client.HTTPConnection("127.0.0.1", provider.port, timeout=5)
        payload = {
            "model": "acceptance-fake-model",
            "messages": [
                {"role": "system", "content": "中文会议实时理解引擎"},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "new_paragraphs": [
                                {
                                    "id": "paragraph-1",
                                    "revision": 1,
                                    "text": "欢迎来到摩哒社区。",
                                }
                            ]
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
        }
        connection.request(
            "POST",
            "/v1/chat/completions",
            body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {credential}",
                "Content-Type": "application/json",
            },
        )
        response = connection.getresponse()
        body = json.loads(response.read().decode("utf-8"))
        connection.close()

        assert response.status == 200
        content = json.loads(body["choices"][0]["message"]["content"])
        assert (
            content["paragraph_revisions"][0]["corrected_text"] == "欢迎来到魔搭社区。"
        )
        assert content["paragraph_revisions"][0]["change_count"] == 1
        assert content["topic_update"]["evidence_segment_ids"] == ["paragraph-1"]
        assert content["topic_update"]["evidence_quote"] == "欢迎来到摩哒社区。"
        assert content["follow_up"]["evidence_segment_ids"] == ["paragraph-1"]
        assert provider.metadata() == {
            "kind": "fake_openai_compatible_gateway",
            "is_fake": True,
            "is_real_llm": False,
            "request_count": 1,
            "purposes": {"realtime_intelligence": 1},
            "all_requests_authenticated": True,
        }
        assert credential not in json.dumps(provider.requests, ensure_ascii=False)
    finally:
        provider.stop()


def test_missing_packaged_binary_returns_and_writes_a_sanitized_no_go_report(tmp_path):
    repo_root = tmp_path / "repo"
    output_root = repo_root / "artifacts" / "tmp" / "acceptance"
    repo_root.mkdir()
    audio_path = repo_root / "fixture.wav"
    audio_path.write_bytes(b"not-a-real-wave")

    report = acceptance.run_acceptance(
        repo_root=repo_root,
        app_path=repo_root / "Missing Meeting Copilot.app",
        audio_path=audio_path,
        output_root=output_root,
        run_id="missing-app",
    )

    assert report["status"] == "no_go"
    assert "packaged_app_binary_missing" in report["blockers"]
    assert report["report_path"] == ("artifacts/tmp/acceptance/missing-app/report.json")
    serialized = json.dumps(report, ensure_ascii=False)
    assert str(tmp_path) not in serialized
    written = json.loads(
        (output_root / "missing-app" / "report.json").read_text(encoding="utf-8")
    )
    assert written["status"] == "no_go"
    assert str(tmp_path) not in json.dumps(written, ensure_ascii=False)
