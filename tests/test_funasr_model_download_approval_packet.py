import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = REPO_ROOT / "code" / "asr_runtime" / "funasr-model-download-approval.policy.json"
TOOL_PATH = REPO_ROOT / "tools" / "funasr_model_download_approval_packet.py"

MODEL_ID = "speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online"
MODEL_URL = f"https://www.modelscope.cn/models/iic/{MODEL_ID}"

SAFETY_FLAGS = [
    "safe_to_execute_download_now",
    "safe_to_download_models_now",
    "safe_to_run_modelscope_now",
    "safe_to_run_python_download_now",
    "safe_to_modify_shell_profile_now",
    "safe_to_read_configs_local_now",
    "safe_to_read_user_audio_now",
    "safe_to_call_remote_asr_now",
    "safe_to_call_llm_now",
    "safe_to_run_funasr_smoke_now",
]

REQUIRED_APPROVAL_TOKENS = [
    "explicit_user_approval_for_funasr_model_download",
    "approved_model_provider_modelscope_iic",
    "approved_model_id_speech_paraformer_online",
    "approved_network_download_policy_for_modelscope",
    "approved_target_cache_root_policy",
    "approved_disk_growth_and_cleanup_policy",
    "approved_manual_user_run_only_boundary",
    "no_private_audio_secret_remote_boundary_reconfirmed",
]

POST_DOWNLOAD_VERIFICATION_ORDER = [
    "local_model_dir_exists",
    "required_model_files_present",
    "funasr_synthetic_smoke_readiness_gate",
    "transcribe_funasr_streaming_offline_guard",
    "synthetic_product_value_gate",
]


def load_tool_module():
    spec = importlib.util.spec_from_file_location("funasr_model_download_approval_packet", TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_policy() -> dict:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def test_funasr_model_download_policy_exists_and_records_manual_boundary():
    policy = load_policy()

    assert policy["drv_id"] == "DRV-019"
    assert policy["policy_name"] == "FunASR Model Download Approval Packet Policy"
    assert policy["policy_status"] == "funasr_model_download_approval_packet_policy_only"
    assert policy["default_quality_gate_status"] == "included_in_root_pytest"
    assert policy["approval_packet_mode"] == "manual_user_run_only"
    assert policy["model_provider"] == "ModelScope"
    assert policy["model_namespace"] == "iic"
    assert policy["model_id"] == MODEL_ID
    assert policy["model_url"] == MODEL_URL
    assert policy["expected_model_size_note"] == "about_840mb_observed_online_streaming_model"
    assert policy["manual_instruction_text_status"] == "inert_text_only"
    assert policy["command_execution_status"] == "not_run"
    assert policy["model_download_execution_status"] == "not_run"
    assert policy["target_cache_root_policy"] == "user_selected_or_modelscope_default_iic_runtime_cache"
    assert policy["required_model_files_after_download"] == ["model.pt", "config.yaml"]
    for field in SAFETY_FLAGS:
        assert policy[field] is False

    assert policy["required_approval_tokens_before_download"] == REQUIRED_APPROVAL_TOKENS
    assert policy["post_download_verification_order"] == POST_DOWNLOAD_VERIFICATION_ORDER


def test_funasr_policy_records_inert_manual_commands_sources_risks_and_cleanup():
    policy = load_policy()

    source_urls = {source["url"] for source in policy["official_sources"]}
    assert MODEL_URL in source_urls
    assert "https://github.com/modelscope/FunASR" in source_urls

    manual_text = policy["manual_instruction_text"]
    assert manual_text["execution_boundary"] == "manual_user_run_only"
    assert manual_text["manual_command_text"].startswith("python -m modelscope download")
    assert f"--model iic/{MODEL_ID}" in manual_text["manual_command_text"]
    assert "--local_dir <user-approved-funasr-model-dir>" in manual_text["manual_command_text"]
    assert manual_text["manual_placeholder_text"] == (
        "manual_user_download_modelscope_model_to_<approved_local_model_dir>"
    )

    assert "model_download_can_add_about_840mb_or_more_to_disk" in policy["risk_notes"]
    assert "delete_user_approved_model_dir_if_download_is_rejected_or_failed" in policy["cleanup_notes"]
    assert "do_not_use_configs_local_for_model_download" in policy["forbidden_default_side_effects"]
    assert "microphone_capture" in policy["forbidden_default_side_effects"]


def test_funasr_approval_report_is_static_and_never_executes_manual_text():
    tool = load_tool_module()

    report = tool.build_funasr_model_download_approval_packet(policy_path=POLICY_PATH)

    assert report["drv_id"] == "DRV-019"
    assert report["report_mode"] == "funasr_model_download_approval_packet_static_report"
    assert report["policy_validation_status"] == "passed"
    assert report["approval_packet_status"] == "generated_for_manual_review"
    assert report["execution_mode"] == "manual_user_run_only"
    assert report["manual_instruction_text_status"] == "inert_text_only"
    assert report["command_execution_status"] == "not_run"
    assert report["external_command_execution_status"] == "not_run"
    assert report["model_download_execution_status"] == "not_run"
    assert report["model_provider"] == "ModelScope"
    assert report["model_id"] == MODEL_ID
    assert report["model_url"] == MODEL_URL
    assert report["approval_blockers"] == REQUIRED_APPROVAL_TOKENS
    assert report["post_download_verification_order"] == POST_DOWNLOAD_VERIFICATION_ORDER
    assert report["manual_instruction_text"]["execution_boundary"] == "manual_user_run_only"
    for field in SAFETY_FLAGS:
        assert report[field] is False

    report_json = json.dumps(report, ensure_ascii=False)
    assert "/Users/" not in report_json
    assert "sk-" not in report_json
    assert "Bearer " not in report_json
    assert "configs/local" not in report_json


def test_custom_policy_cannot_relax_safety_flags_remove_tokens_or_enable_execution(tmp_path):
    tool = load_tool_module()
    custom_policy = load_policy()
    for field in SAFETY_FLAGS:
        custom_policy[field] = True
    custom_policy["approval_packet_mode"] = "execute_download"
    custom_policy["command_execution_status"] = "ready"
    custom_policy["model_download_execution_status"] = "ready"
    custom_policy["manual_instruction_text_status"] = "executable"
    custom_policy["required_approval_tokens_before_download"] = REQUIRED_APPROVAL_TOKENS[:-1]
    custom_policy["official_sources"] = []
    custom_policy["manual_instruction_text"]["execution_boundary"] = "execute_by_tool"
    custom_policy_path = tmp_path / "funasr-model-download-approval.policy.json"
    custom_policy_path.write_text(json.dumps(custom_policy), encoding="utf-8")

    report = tool.build_funasr_model_download_approval_packet(policy_path=custom_policy_path)

    assert report["policy_validation_status"] == "failed"
    assert set(report["policy_validation_errors"]) >= {
        f"{field} must be false" for field in SAFETY_FLAGS
    }
    assert "approval_packet_mode must be manual_user_run_only" in report["policy_validation_errors"]
    assert "command_execution_status must be not_run" in report["policy_validation_errors"]
    assert "model_download_execution_status must be not_run" in report["policy_validation_errors"]
    assert "manual_instruction_text_status must be inert_text_only" in report[
        "policy_validation_errors"
    ]
    assert "required_approval_tokens_before_download must match DRV-019 required tokens" in report[
        "policy_validation_errors"
    ]
    assert "official_sources must contain required DRV-019 official URLs" in report[
        "policy_validation_errors"
    ]
    assert "manual_instruction_text must keep ModelScope download instructions as inert text" in report[
        "policy_validation_errors"
    ]
    assert report["approval_packet_status"] == "blocked_by_policy_validation"
    assert report["command_execution_status"] == "not_run"
    assert report["model_download_execution_status"] == "not_run"
    assert {source["url"] for source in report["official_sources"]} == {
        MODEL_URL,
        "https://github.com/modelscope/FunASR",
    }
    assert report["manual_instruction_text"]["execution_boundary"] == "manual_user_run_only"
    for field in SAFETY_FLAGS:
        assert report[field] is False


def test_custom_policy_path_rejects_forbidden_roots_before_reading(tmp_path, monkeypatch):
    tool = load_tool_module()
    monkeypatch.setattr(tool, "REPO_ROOT", tmp_path)

    forbidden_roots = {
        ("configs", "local"): "configs/local",
        ("data", "asr_eval", "local_samples"): "data/asr_eval/local_samples",
        ("data", "local_runtime"): "data/local_runtime",
        ("outputs",): "outputs",
        ("artifacts", "tmp"): "artifacts/tmp",
    }

    for suffix_parts, label in forbidden_roots.items():
        forbidden_policy_path = tmp_path.joinpath(*suffix_parts) / "funasr-model-download-approval.policy.json"

        report = tool.build_funasr_model_download_approval_packet(policy_path=forbidden_policy_path)

        assert report["drv_id"] == "DRV-019"
        assert report["policy_validation_status"] == "failed"
        assert report["policy_validation_errors"] == [
            f"policy path is blocked: {label}",
        ]
        assert report["policy_read_status"] == "blocked"
        assert report["approval_packet_status"] == "blocked_by_policy_validation"
        assert report["command_execution_status"] == "not_run"
        assert report["model_download_execution_status"] == "not_run"
        for field in SAFETY_FLAGS:
            assert report[field] is False


def test_custom_policy_path_rejects_symlink_to_forbidden_root_before_reading(tmp_path, monkeypatch):
    tool = load_tool_module()
    repo_root = tmp_path / "repo"
    forbidden_root = tmp_path / "outside" / "configs" / "local"
    visible_root = repo_root / "code" / "asr_runtime"
    forbidden_root.mkdir(parents=True)
    visible_root.mkdir(parents=True)
    monkeypatch.setattr(tool, "REPO_ROOT", repo_root)

    target = forbidden_root / "funasr-model-download-approval.policy.json"
    target.write_text("{}", encoding="utf-8")
    link = visible_root / "linked-policy.json"
    link.symlink_to(target)

    report = tool.build_funasr_model_download_approval_packet(policy_path=link)

    assert report["policy_validation_status"] == "failed"
    assert report["policy_validation_errors"] == [
        "policy path is blocked: configs/local",
    ]
    assert report["policy_read_status"] == "blocked"
    assert report["approval_packet_status"] == "blocked_by_policy_validation"


def test_funasr_approval_tool_source_has_no_command_execution_entrypoints():
    source = TOOL_PATH.read_text(encoding="utf-8")

    forbidden_source_tokens = [
        "subprocess",
        "os.system",
        "Popen",
        "check_call",
        "check_output",
        "run(",
        "exec(",
        "eval(",
    ]
    for token in forbidden_source_tokens:
        assert token not in source
