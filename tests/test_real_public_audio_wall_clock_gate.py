import importlib.util
import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "real_public_audio_wall_clock_gate.py"


def load_tool_module():
    spec = importlib.util.spec_from_file_location("real_public_audio_wall_clock_gate", TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def repaired_quality_report() -> dict:
    return {
        "default_decision": {
            "decision_status": "candidate_for_next_real_audio_gate",
            "blockers": [],
        },
        "privacy_cost_flags": {
            "remote_asr_called": False,
            "llm_called": False,
            "raw_audio_uploaded": False,
            "user_audio_committed_to_repo": False,
        },
        "aggregate": {
            "sample_count": 4,
            "sample_provider_count": 4,
            "pipeline_closed_sample_provider_count": 4,
            "quality_pass_sample_provider_count": 4,
            "samples_with_quality_pass_count": 4,
            "suspected_reference_artifact_mismatch_sample_count": 0,
        },
    }


def synthetic_soak_report() -> dict:
    return {
        "schema_version": "long_meeting_soak_report.v1",
        "verdict": "go",
        "duration_minutes": 20,
        "expected_audio_seconds": 1200,
        "asr_rtf": 0.1,
        "privacy_cost_flags": {
            "remote_asr_called": False,
            "raw_audio_uploaded": False,
            "real_microphone_started": False,
            "llm_called": False,
        },
    }


def short_real_mic_manifest() -> dict:
    return {
        "verdict": "go",
        "input_audio_path_kind": "browser_get_user_media",
        "asr_provider": "sherpa_onnx_realtime",
        "asr_provider_mode": "real",
        "asr_semantic_quality_status": "passed",
        "delete_verified": True,
        "privacy_cost_flags": {
            "remote_asr_called": False,
            "raw_audio_uploaded": False,
            "configs_local_read": False,
        },
    }


def wall_clock_soak_report(**overrides) -> dict:
    report = {
        "schema_version": "real_public_audio_wall_clock_soak.v1",
        "verdict": "go",
        "source_kind": "real_microphone",
        "duration_minutes": 20,
        "asr_provider_mode": "real",
        "pipeline_closed": True,
        "quality_gate_passed": True,
        "privacy_cost_flags": {
            "remote_asr_called": False,
            "raw_audio_uploaded": False,
            "configs_local_read": False,
        },
    }
    report.update(overrides)
    return report


def test_gate_blocks_when_only_synthetic_soak_and_short_real_mic_are_available(tmp_path: Path):
    tool = load_tool_module()
    quality = write_json(tmp_path / "quality.json", repaired_quality_report())
    soak = write_json(tmp_path / "soak.json", synthetic_soak_report())
    real_mic = write_json(tmp_path / "real-mic.json", short_real_mic_manifest())
    output = tmp_path / "gate.json"

    report = tool.build_real_public_audio_wall_clock_gate_report(
        quality_report_path=quality,
        synthetic_soak_report_path=soak,
        real_mic_manifest_path=real_mic,
        output_path=output,
    )

    assert output.exists()
    assert report["schema_version"] == "real_public_audio_wall_clock_gate.v1"
    assert report["gate_status"] == "blocked_real_or_public_wall_clock_soak_missing"
    assert "real_or_public_wall_clock_soak_missing" in report["blockers"]
    assert report["repaired_synthetic_quality"]["ready"] is True
    assert report["synthetic_soak"]["ready"] is True
    assert report["synthetic_soak"]["counts_as_real_or_public_wall_clock_soak"] is False
    assert report["real_mic_short"]["ready"] is True
    assert report["real_mic_short"]["counts_as_wall_clock_soak"] is False
    assert report["wall_clock_soak"]["ready"] is False
    assert report["recommended_next_action"] == "run_real_microphone_or_public_audio_wall_clock_soak"


def test_gate_goes_with_real_or_public_wall_clock_soak_report(tmp_path: Path):
    tool = load_tool_module()
    quality = write_json(tmp_path / "quality.json", repaired_quality_report())
    wall_clock = write_json(tmp_path / "wall-clock.json", wall_clock_soak_report(source_kind="public_audio"))

    report = tool.build_real_public_audio_wall_clock_gate_report(
        quality_report_path=quality,
        wall_clock_soak_report_path=wall_clock,
        output_path=tmp_path / "gate.json",
    )

    assert report["gate_status"] == "candidate_for_real_meeting_pilot"
    assert report["blockers"] == []
    assert report["wall_clock_soak"]["ready"] is True
    assert report["wall_clock_soak"]["source_kind"] == "public_audio"


def test_gate_cli_writes_report_and_returns_nonzero_for_missing_wall_clock(tmp_path: Path):
    quality = write_json(tmp_path / "quality.json", repaired_quality_report())
    output = tmp_path / "gate.json"

    completed = subprocess.run(
        [
            sys.executable,
            str(TOOL_PATH),
            "--quality-report",
            str(quality),
            "--output",
            str(output),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 2
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["gate_status"] == "blocked_real_or_public_wall_clock_soak_missing"
    assert "real_or_public_wall_clock_soak_missing" in completed.stdout
