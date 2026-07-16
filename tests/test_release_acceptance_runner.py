import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "release_acceptance_runner.py"


def load_tool_module():
    spec = importlib.util.spec_from_file_location(
        "release_acceptance_runner",
        TOOL_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def passing_checks():
    return [
        {"name": "pytest_backend_mainline", "status": "passed"},
        {"name": "workbench_smoke", "status": "passed"},
        {"name": "git_diff_check", "status": "passed"},
        {"name": "health_endpoint", "status": "passed", "status_code": 200},
        {"name": "workbench_js_version", "status": "passed", "version": "20260708-p0"},
    ]


def go_manifest(**overrides):
    manifest = {
        "verdict": "go",
        "degradation_reasons": [],
        "audio_source": "uploaded_wav",
        "llm_provider": "real_gateway",
        "asr_provider_mode": "real",
        "asr_fallback_used": False,
        "privacy_cost_flags": {
            "raw_audio_uploaded": False,
            "remote_asr_called": False,
            "llm_called": True,
            "configs_local_read": False,
            "user_audio_committed_to_repo": False,
        },
    }
    manifest.update(overrides)
    return manifest


def test_release_acceptance_runner_blocks_when_required_lane_is_no_go(tmp_path):
    tool = load_tool_module()

    result = tool.run_release_acceptance(
        run_id="release-no-go-lane",
        artifact_root=tmp_path / "release",
        quality_checks=passing_checks(),
        lane_results={
            "file_lane": {"manifest": go_manifest(audio_source="uploaded_wav")},
            "simulated_realtime": {
                "manifest": go_manifest(
                    verdict="no_go",
                    audio_source="simulated_realtime_wav",
                    degradation_reasons=["asr_semantic_quality_blocked"],
                )
            },
            "real_mic_recorded_realtime": {
                "manifest": go_manifest(
                    audio_source="real_mic_recorded_wav",
                    counts_as_real_mic_go_evidence=True,
                )
            },
            "browser_live_mic": {
                "manifest": go_manifest(
                    audio_source="browser_live_mic",
                    browser_live_mic_go_evidence=True,
                    counts_as_real_mic_go_evidence=True,
                )
            },
        },
    )

    summary = result["summary"]
    assert summary["verdict"] == "no_go"
    assert "lane_simulated_realtime_no_go" in summary["blockers"]
    assert "asr_semantic_quality_blocked" in summary["blockers"]
    assert (Path(result["artifact_root"]) / "summary.json").exists()
    assert (Path(result["artifact_root"]) / "report.md").exists()


def test_release_acceptance_runner_requires_browser_live_mic_go_evidence(tmp_path):
    tool = load_tool_module()

    result = tool.run_release_acceptance(
        run_id="release-browser-missing",
        artifact_root=tmp_path / "release",
        quality_checks=passing_checks(),
        lane_results={
            "file_lane": {"manifest": go_manifest(audio_source="uploaded_wav")},
            "simulated_realtime": {"manifest": go_manifest(audio_source="simulated_realtime_wav")},
            "real_mic_recorded_realtime": {
                "manifest": go_manifest(
                    audio_source="real_mic_recorded_wav",
                    counts_as_real_mic_go_evidence=True,
                )
            },
            "browser_live_mic": {
                "manifest": go_manifest(
                    verdict="no_go",
                    audio_source="browser_live_mic",
                    browser_live_mic_go_evidence=False,
                    degradation_reasons=["browser_live_mic_not_proven"],
                )
            },
        },
    )

    summary = result["summary"]
    assert summary["verdict"] == "no_go"
    assert "blocked_browser_live_mic_not_proven" in summary["blockers"]
    assert "browser_live_mic_not_proven" in summary["blockers"]
    assert summary["lanes"]["browser_live_mic"]["browser_live_mic_go_evidence"] is False


def test_release_acceptance_runner_blocks_browser_live_mic_production_llm_without_usage(tmp_path):
    tool = load_tool_module()

    result = tool.run_release_acceptance(
        run_id="release-browser-production-llm-missing-usage",
        artifact_root=tmp_path / "release",
        quality_checks=passing_checks(),
        lane_results={
            "file_lane": {"manifest": go_manifest(audio_source="uploaded_wav")},
            "simulated_realtime": {"manifest": go_manifest(audio_source="simulated_realtime_wav")},
            "real_mic_recorded_realtime": {
                "manifest": go_manifest(
                    audio_source="real_mic_recorded_wav",
                    counts_as_real_mic_go_evidence=True,
                )
            },
            "browser_live_mic": {
                "manifest": go_manifest(
                    audio_source="browser_live_mic",
                    browser_live_mic_go_evidence=True,
                    counts_as_real_mic_go_evidence=True,
                    derivation_mode="production_enabled",
                    llm_called=True,
                    llm_provider="real_gateway",
                    gateway_base_url_kind="remote",
                    counts_as_production_llm_evidence=False,
                    llm_call_count=0,
                    llm_usage_total_tokens=0,
                )
            },
        },
    )

    summary = result["summary"]
    assert summary["verdict"] == "no_go"
    assert "browser_live_mic_production_llm_evidence_missing" in summary["blockers"]
    assert "browser_live_mic_llm_usage_evidence_missing" in summary["blockers"]
    assert summary["lanes"]["browser_live_mic"]["counts_as_production_llm_evidence"] is False


def test_release_acceptance_runner_go_when_all_required_checks_and_lanes_pass(tmp_path):
    tool = load_tool_module()

    result = tool.run_release_acceptance(
        run_id="release-go",
        artifact_root=tmp_path / "release",
        quality_checks=passing_checks(),
        lane_results={
            "file_lane": {"manifest": go_manifest(audio_source="uploaded_wav")},
            "simulated_realtime": {"manifest": go_manifest(audio_source="simulated_realtime_wav")},
            "real_mic_recorded_realtime": {
                "manifest": go_manifest(
                    audio_source="real_mic_recorded_wav",
                    counts_as_real_mic_go_evidence=True,
                )
            },
            "browser_live_mic": {
                "manifest": go_manifest(
                    audio_source="browser_live_mic",
                    browser_live_mic_go_evidence=True,
                    counts_as_real_mic_go_evidence=True,
                )
            },
        },
    )

    summary = result["summary"]
    assert summary["verdict"] == "go"
    assert summary["blockers"] == []
    assert summary["lanes"]["browser_live_mic"]["browser_live_mic_go_evidence"] is True
    persisted = json.loads((Path(result["artifact_root"]) / "summary.json").read_text(encoding="utf-8"))
    assert persisted["verdict"] == "go"
    report = (Path(result["artifact_root"]) / "report.md").read_text(encoding="utf-8")
    assert "Verdict: go" in report


def test_release_acceptance_runner_treats_recorded_real_mic_as_optional_when_browser_live_mic_is_go(tmp_path):
    tool = load_tool_module()

    result = tool.run_release_acceptance(
        run_id="release-browser-real-mic-go-recorded-optional",
        artifact_root=tmp_path / "release",
        quality_checks=passing_checks(),
        lane_results={
            "file_lane": {"manifest": go_manifest(audio_source="uploaded_wav")},
            "simulated_realtime": {"manifest": go_manifest(audio_source="simulated_realtime_wav")},
            "real_mic_recorded_realtime": {
                "manifest": go_manifest(
                    verdict="no_go",
                    audio_source="real_mic_recorded_wav",
                    counts_as_real_mic_go_evidence=False,
                    degradation_reasons=["real_mic_recorded_inputs_missing"],
                )
            },
            "browser_live_mic": {
                "manifest": go_manifest(
                    audio_source="browser_live_mic",
                    browser_live_mic_go_evidence=True,
                    counts_as_real_mic_go_evidence=True,
                )
            },
        },
    )

    summary = result["summary"]
    assert summary["verdict"] == "go"
    assert "lane_real_mic_recorded_realtime_no_go" not in summary["blockers"]
    assert "real_mic_recorded_inputs_missing" not in summary["blockers"]
    assert "real_mic_recorded_realtime_not_proven" not in summary["blockers"]
    assert summary["lanes"]["browser_live_mic"]["browser_live_mic_go_evidence"] is True
    assert summary["lanes"]["real_mic_recorded_realtime"]["verdict"] == "no_go"


def test_release_acceptance_summary_includes_traceability_and_privacy_cost_flags(tmp_path):
    tool = load_tool_module()

    result = tool.run_release_acceptance(
        run_id="release-traceability",
        artifact_root=tmp_path / "release",
        quality_checks=passing_checks(),
        lane_results={
            "file_lane": {
                "artifact_root": "artifacts/tmp/acceptance/file",
                "manifest": go_manifest(audio_source="uploaded_wav"),
            },
            "simulated_realtime": {
                "artifact_root": "artifacts/tmp/acceptance/sim",
                "manifest": go_manifest(audio_source="simulated_realtime_wav"),
            },
            "real_mic_recorded_realtime": {
                "artifact_root": "artifacts/tmp/acceptance/real-recorded",
                "manifest": go_manifest(
                    audio_source="real_mic_recorded_wav",
                    counts_as_real_mic_go_evidence=True,
                ),
            },
            "browser_live_mic": {
                "artifact_root": "artifacts/tmp/acceptance/browser",
                "manifest": go_manifest(
                    audio_source="browser_live_mic",
                    browser_live_mic_go_evidence=True,
                    counts_as_real_mic_go_evidence=True,
                ),
            },
        },
    )

    summary = result["summary"]
    assert summary["git_commit"]
    assert summary["privacy_cost_flags"] == {
        "raw_audio_uploaded": False,
        "remote_asr_called": False,
        "llm_called": True,
        "configs_local_read": False,
        "user_audio_committed_to_repo": False,
    }
    assert summary["llm_call_count"] == 0
    assert summary["llm_usage_total_tokens"] == 0
    assert summary["artifacts"]["summary_json"] == "summary.json"
    assert summary["artifacts"]["report_md"] == "report.md"
    assert summary["lanes"]["browser_live_mic"]["manifest_path"] == "artifacts/tmp/acceptance/browser/manifest.json"
    assert summary["lanes"]["browser_live_mic"]["go_no_go_path"] == "artifacts/tmp/acceptance/browser/go_no_go.md"


def test_release_acceptance_privacy_cost_flags_use_top_level_llm_called(tmp_path):
    tool = load_tool_module()

    result = tool.run_release_acceptance(
        run_id="release-llm-called-aggregation",
        artifact_root=tmp_path / "release",
        quality_checks=passing_checks(),
        lane_results={
            "file_lane": {
                "manifest": go_manifest(
                    audio_source="uploaded_wav",
                    llm_called=True,
                    privacy_cost_flags={},
                )
            },
            "simulated_realtime": {
                "manifest": go_manifest(
                    audio_source="simulated_realtime_wav",
                    llm_called=True,
                    privacy_cost_flags={"llm_called": False},
                )
            },
            "real_mic_recorded_realtime": {
                "manifest": go_manifest(
                    audio_source="real_mic_recorded_wav",
                    counts_as_real_mic_go_evidence=True,
                    llm_called=True,
                    privacy_cost_flags={"llm_called": False},
                )
            },
            "browser_live_mic": {
                "manifest": go_manifest(
                    audio_source="browser_live_mic",
                    browser_live_mic_go_evidence=True,
                    counts_as_real_mic_go_evidence=True,
                    llm_called=True,
                    privacy_cost_flags={"llm_called": False},
                )
            },
        },
    )

    assert result["summary"]["privacy_cost_flags"]["llm_called"] is True
    assert result["summary"]["lanes"]["file_lane"]["llm_called"] is True


def test_release_acceptance_sums_lane_llm_usage(tmp_path):
    tool = load_tool_module()

    result = tool.run_release_acceptance(
        run_id="release-usage-summary",
        artifact_root=tmp_path / "release",
        quality_checks=passing_checks(),
        lane_results={
            "file_lane": {"manifest": go_manifest(audio_source="uploaded_wav", llm_call_count=2, llm_usage_total_tokens=100)},
            "simulated_realtime": {"manifest": go_manifest(audio_source="simulated_realtime_wav", llm_call_count=3, llm_usage_total_tokens=150)},
            "real_mic_recorded_realtime": {
                "manifest": go_manifest(
                    audio_source="real_mic_recorded_wav",
                    counts_as_real_mic_go_evidence=True,
                    llm_call_count=4,
                    llm_usage_total_tokens=200,
                )
            },
            "browser_live_mic": {
                "manifest": go_manifest(
                    audio_source="browser_live_mic",
                    browser_live_mic_go_evidence=True,
                    counts_as_real_mic_go_evidence=True,
                    llm_call_count=5,
                    llm_usage_total_tokens=250,
                )
            },
        },
    )

    assert result["summary"]["llm_call_count"] == 14
    assert result["summary"]["llm_usage_total_tokens"] == 700


def test_command_check_records_timeout_as_failed_without_raising(tmp_path, monkeypatch):
    tool = load_tool_module()

    def fake_run(*args, **kwargs):
        raise tool.subprocess.TimeoutExpired(cmd=["slow"], timeout=1)

    monkeypatch.setattr(tool.subprocess, "run", fake_run)

    result = tool._run_command_check(
        name="slow_check",
        command=["slow"],
        artifact_root=tmp_path,
        timeout_seconds=1,
    )

    assert result["status"] == "failed"
    assert result["returncode"] is None
    assert result["error"] == "timeout"


def test_release_summary_writer_redacts_secret_values(tmp_path, monkeypatch):
    tool = load_tool_module()
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-release-secret")
    output = tmp_path / "summary.json"

    tool._write_json(
        output,
        {
            "api_key": "sk-release-secret",
            "authorization": "Bearer sk-release-secret",
            "nested": {"text": "token=sk-release-secret"},
        },
    )

    serialized = output.read_text(encoding="utf-8")
    assert "sk-release-secret" not in serialized
    assert "<redacted>" in serialized
