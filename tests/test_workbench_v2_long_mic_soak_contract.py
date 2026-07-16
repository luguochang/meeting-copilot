import hashlib
import json
import subprocess
import wave
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER = REPO_ROOT / "code" / "web_mvp" / "e2e" / "workbench_v2_long_mic_soak.mjs"


def _evaluate_input_metadata(tmp_path: Path, payload: dict) -> dict:
    input_path = tmp_path / "input-metadata.json"
    input_path.write_text(json.dumps(payload), encoding="utf-8")
    completed = subprocess.run(
        ["node", str(RUNNER), "--evaluate-input-metadata-contract", str(input_path)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def _playlist_manifest(
    *,
    playback_count: int,
    total_played_seconds: float,
    source_audio_sha256: str = "a" * 64,
    source_audio_duration_seconds: float = 26.66,
) -> dict:
    return {
        "schema_version": "workbench-v2-soak-playlist.v1",
        "strategy": "repeat_single_source_until_deadline",
        "source_count": 1,
        "sources": [
            {
                "source_audio_sha256": source_audio_sha256,
                "source_audio_duration_seconds": source_audio_duration_seconds,
            }
        ],
        "playback_count": playback_count,
        "total_played_seconds": total_played_seconds,
        "entries": [
            {
                "playback_index": index + 1,
                "source_audio_sha256": source_audio_sha256,
                "played_seconds": total_played_seconds / playback_count,
            }
            for index in range(playback_count)
        ],
    }


def test_long_mic_report_separates_recording_and_post_processing_time():
    source = RUNNER.read_text(encoding="utf-8")

    assert "recordingStoppedAtMs" in source
    assert "recording_wall_clock_seconds" in source
    assert "post_processing_seconds" in source
    assert "total_wall_clock_seconds" in source
    assert "post_processing_duration_exceeded" in source
    assert "waitForChildExit" in source
    assert "audio_playback_stop_timeout" in source
    assert "cdp_operation_timeout" in source
    assert 'phase("end_requested")' in source
    assert "setTimeout(() => button.click(), 0)" in source
    assert "waitForMeetingEnded" in source
    assert 'page.on("Page.javascriptDialogOpening"' in source
    assert 'page.send("Page.handleJavaScriptDialog"' in source
    assert 'container.style.scrollBehavior = "auto"' in source
    assert "requestAnimationFrame(() => requestAnimationFrame(resolve))" in source
    assert "requireFullReview" in source
    assert "formal_derivation_suppressed" in source
    assert "review_jobs_incomplete" in source
    assert "suggestion_lane_complete" in source
    assert 'job.status === "cancelled" && job.error_class === "evidence_superseded"' in source
    assert "allowlisted_network_cancellations" in source
    assert "diagnostics.network_failures.length" in source
    assert 'params.type === "EventSource"' in source


def test_long_mic_report_emits_input_semantic_eligibility_metadata():
    source = RUNNER.read_text(encoding="utf-8")

    for field in (
        "source_audio_duration_seconds",
        "source_audio_sha256",
        "playback_count",
        "playlist_manifest",
        "repetition_ratio",
        "semantic_acceptance_eligible",
        "semantic_acceptance_blockers",
        "counts_as_phase2_semantic_gate",
    ):
        assert field in source

    assert 'createHash("sha256")' in source
    assert "parseWavDurationSeconds" in source
    assert "semantic_acceptance_input_ineligible" in source
    assert "requireOneHour && !report.semantic_acceptance_eligible" in source
    assert '"real_browser_microphone_with_unique_source_audio"' in source
    assert '"real_browser_microphone_with_repeated_acoustic_fixture"' in source


def test_input_audio_probe_reports_wav_duration_and_sha256(tmp_path):
    audio_path = tmp_path / "source.wav"
    sample_rate = 16_000
    with wave.open(str(audio_path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(b"\x00\x00" * sample_rate * 2)

    completed = subprocess.run(
        ["node", str(RUNNER), "--probe-input-audio", str(audio_path)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout)

    assert result["source_audio_duration_seconds"] == 2
    assert result["source_audio_sha256"] == hashlib.sha256(audio_path.read_bytes()).hexdigest()


def test_short_highly_repeated_audio_is_not_semantic_acceptance_eligible(tmp_path):
    repetition_ratio = (3_600 - 26.66) / 3_600
    result = _evaluate_input_metadata(
        tmp_path,
        {
            "target_duration_seconds": 3_600,
            "source_audio_duration_seconds": 26.66,
            "source_audio_sha256": "a" * 64,
            "playback_count": 136,
            "playlist_manifest": _playlist_manifest(
                playback_count=136,
                total_played_seconds=3_600,
            ),
            "repetition_ratio": repetition_ratio,
        },
    )

    assert result["semantic_acceptance_eligible"] is False
    assert result["required_unique_audio_seconds"] == 300
    assert result["max_repetition_ratio"] == 0.1
    assert "source_audio_too_short" in result["semantic_acceptance_blockers"]
    assert "repetition_ratio_exceeded" in result["semantic_acceptance_blockers"]


def test_low_repetition_audio_can_be_semantic_acceptance_eligible(tmp_path):
    result = _evaluate_input_metadata(
        tmp_path,
        {
            "target_duration_seconds": 3_600,
            "source_audio_duration_seconds": 3_500,
            "source_audio_sha256": "b" * 64,
            "playback_count": 2,
            "playlist_manifest": {
                **_playlist_manifest(playback_count=2, total_played_seconds=3_600),
                "sources": [
                    {
                        "source_audio_sha256": "b" * 64,
                        "source_audio_duration_seconds": 3_500,
                    }
                ],
                "entries": [
                    {
                        "playback_index": 1,
                        "source_audio_sha256": "b" * 64,
                        "played_seconds": 3_500,
                    },
                    {
                        "playback_index": 2,
                        "source_audio_sha256": "b" * 64,
                        "played_seconds": 100,
                    },
                ],
            },
            "repetition_ratio": 100 / 3_600,
        },
    )

    assert result["semantic_acceptance_eligible"] is True
    assert result["semantic_acceptance_blockers"] == []


def test_declared_repetition_ratio_must_match_playlist(tmp_path):
    result = _evaluate_input_metadata(
        tmp_path,
        {
            "target_duration_seconds": 3_600,
            "source_audio_duration_seconds": 3_500,
            "source_audio_sha256": "b" * 64,
            "playback_count": 2,
            "playlist_manifest": {
                **_playlist_manifest(
                    playback_count=2,
                    total_played_seconds=3_600,
                    source_audio_sha256="b" * 64,
                    source_audio_duration_seconds=3_500,
                ),
                "entries": [
                    {
                        "playback_index": 1,
                        "source_audio_sha256": "b" * 64,
                        "played_seconds": 1_800,
                    },
                    {
                        "playback_index": 2,
                        "source_audio_sha256": "b" * 64,
                        "played_seconds": 1_800,
                    },
                ],
            },
            "repetition_ratio": 0,
        },
    )

    assert result["semantic_acceptance_eligible"] is False
    assert "repetition_ratio_mismatch" in result["semantic_acceptance_blockers"]


def test_missing_input_metadata_fails_closed(tmp_path):
    result = _evaluate_input_metadata(
        tmp_path,
        {
            "target_duration_seconds": 3_600,
            "source_audio_duration_seconds": None,
            "source_audio_sha256": "",
            "playback_count": 0,
            "playlist_manifest": None,
            "repetition_ratio": None,
        },
    )

    assert result["semantic_acceptance_eligible"] is False
    assert set(result["semantic_acceptance_blockers"]) >= {
        "source_audio_duration_unknown",
        "source_audio_sha256_missing",
        "playback_manifest_invalid",
        "repetition_ratio_unknown",
    }
