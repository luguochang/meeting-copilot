#!/usr/bin/env python3
"""Build the DRV-019 FunASR model download approval packet as static JSON."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TextIO


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY_PATH = REPO_ROOT / "code" / "asr_runtime" / "funasr-model-download-approval.policy.json"

MODEL_ID = "speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online"
MODEL_URL = f"https://www.modelscope.cn/models/iic/{MODEL_ID}"
FUNASR_URL = "https://github.com/modelscope/FunASR"
MANUAL_COMMAND_TEXT = (
    "python -m modelscope download "
    f"--model iic/{MODEL_ID} "
    "--local_dir <user-approved-funasr-model-dir>"
)
MANUAL_PLACEHOLDER_TEXT = "manual_user_download_modelscope_model_to_<approved_local_model_dir>"

SAFETY_FLAGS = (
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
)
REQUIRED_APPROVAL_TOKENS = (
    "explicit_user_approval_for_funasr_model_download",
    "approved_model_provider_modelscope_iic",
    "approved_model_id_speech_paraformer_online",
    "approved_network_download_policy_for_modelscope",
    "approved_target_cache_root_policy",
    "approved_disk_growth_and_cleanup_policy",
    "approved_manual_user_run_only_boundary",
    "no_private_audio_secret_remote_boundary_reconfirmed",
)
POST_DOWNLOAD_VERIFICATION_ORDER = (
    "local_model_dir_exists",
    "required_model_files_present",
    "funasr_synthetic_smoke_readiness_gate",
    "transcribe_funasr_streaming_offline_guard",
    "synthetic_product_value_gate",
)
REQUIRED_MODEL_FILES = ("model.pt", "config.yaml")
OFFICIAL_SOURCE_URLS = {MODEL_URL, FUNASR_URL}
FORBIDDEN_POLICY_PATH_LABELS = (
    ("configs/local", ("configs", "local")),
    ("data/asr_eval/local_samples", ("data", "asr_eval", "local_samples")),
    ("data/local_runtime", ("data", "local_runtime")),
    ("outputs", ("outputs",)),
    ("artifacts/tmp", ("artifacts", "tmp")),
)


def _path_has_suffix_parts(path: Path, suffix_parts: tuple[str, ...]) -> bool:
    parts = path.parts
    width = len(suffix_parts)
    return any(parts[index : index + width] == suffix_parts for index in range(len(parts) - width + 1))


def _forbidden_policy_path_errors_for(path: Path) -> list[str]:
    errors: list[str] = []
    for label, suffix_parts in FORBIDDEN_POLICY_PATH_LABELS:
        if _path_has_suffix_parts(path, suffix_parts):
            errors.append(f"policy path is blocked: {label}")
    return errors


def validate_policy_path(policy_path: Path) -> list[str]:
    errors = _forbidden_policy_path_errors_for(policy_path)
    resolved = policy_path.resolve(strict=False)
    for error in _forbidden_policy_path_errors_for(resolved):
        if error not in errors:
            errors.append(error)
    return errors


def load_policy(policy_path: Path = DEFAULT_POLICY_PATH) -> dict[str, object]:
    path_errors = validate_policy_path(policy_path)
    if path_errors:
        raise ValueError(path_errors[0])
    return json.loads(policy_path.read_text(encoding="utf-8"))


def _is_string_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _source_urls(policy: dict[str, object]) -> set[str]:
    official_sources = policy.get("official_sources")
    if not isinstance(official_sources, list):
        return set()
    urls = set()
    for source in official_sources:
        if isinstance(source, dict) and isinstance(source.get("url"), str):
            urls.add(source["url"])
    return urls


def _trusted_official_sources() -> list[dict[str, str]]:
    return [
        {
            "label": "ModelScope iic Paraformer online streaming model",
            "url": MODEL_URL,
        },
        {
            "label": "FunASR project",
            "url": FUNASR_URL,
        },
    ]


def _trusted_manual_instruction_text() -> dict[str, object]:
    return {
        "source": "modelscope_manual_download_reference",
        "manual_command_text": MANUAL_COMMAND_TEXT,
        "manual_placeholder_text": MANUAL_PLACEHOLDER_TEXT,
        "execution_boundary": "manual_user_run_only",
        "notes": [
            "The repository tool must not execute this text.",
            "The approved local model dir must be supplied later as an absolute path.",
            "The model directory must contain model.pt and config.yaml before smoke execution.",
        ],
    }


def _manual_text_is_valid(manual_text: object) -> bool:
    if not isinstance(manual_text, dict):
        return False
    return (
        manual_text.get("manual_command_text") == MANUAL_COMMAND_TEXT
        and manual_text.get("manual_placeholder_text") == MANUAL_PLACEHOLDER_TEXT
        and manual_text.get("execution_boundary") == "manual_user_run_only"
    )


def validate_policy(policy: dict[str, object]) -> list[str]:
    errors: list[str] = []
    if policy.get("drv_id") != "DRV-019":
        errors.append("drv_id must be DRV-019")
    if policy.get("policy_status") != "funasr_model_download_approval_packet_policy_only":
        errors.append("policy_status must be funasr_model_download_approval_packet_policy_only")
    if policy.get("approval_packet_mode") != "manual_user_run_only":
        errors.append("approval_packet_mode must be manual_user_run_only")
    if policy.get("model_provider") != "ModelScope":
        errors.append("model_provider must be ModelScope")
    if policy.get("model_namespace") != "iic":
        errors.append("model_namespace must be iic")
    if policy.get("model_id") != MODEL_ID:
        errors.append("model_id must match DRV-019 FunASR streaming model")
    if policy.get("model_url") != MODEL_URL:
        errors.append("model_url must match DRV-019 ModelScope URL")
    if policy.get("expected_model_size_note") != "about_840mb_observed_online_streaming_model":
        errors.append("expected_model_size_note must record observed model size risk")
    if policy.get("manual_instruction_text_status") != "inert_text_only":
        errors.append("manual_instruction_text_status must be inert_text_only")
    if policy.get("command_execution_status") != "not_run":
        errors.append("command_execution_status must be not_run")
    if policy.get("model_download_execution_status") != "not_run":
        errors.append("model_download_execution_status must be not_run")
    if policy.get("target_cache_root_policy") != "user_selected_or_modelscope_default_iic_runtime_cache":
        errors.append("target_cache_root_policy must match DRV-019 cache policy")
    if policy.get("required_model_files_after_download") != list(REQUIRED_MODEL_FILES):
        errors.append("required_model_files_after_download must match DRV-019 required files")
    for flag in SAFETY_FLAGS:
        if policy.get(flag) is not False:
            errors.append(f"{flag} must be false")
    if policy.get("required_approval_tokens_before_download") != list(REQUIRED_APPROVAL_TOKENS):
        errors.append("required_approval_tokens_before_download must match DRV-019 required tokens")
    if policy.get("post_download_verification_order") != list(POST_DOWNLOAD_VERIFICATION_ORDER):
        errors.append("post_download_verification_order must match DRV-019 verification order")
    if not OFFICIAL_SOURCE_URLS.issubset(_source_urls(policy)):
        errors.append("official_sources must contain required DRV-019 official URLs")
    if not _manual_text_is_valid(policy.get("manual_instruction_text")):
        errors.append("manual_instruction_text must keep ModelScope download instructions as inert text")
    if not _is_string_list(policy.get("risk_notes")):
        errors.append("risk_notes must be a list of strings")
    if not _is_string_list(policy.get("cleanup_notes")):
        errors.append("cleanup_notes must be a list of strings")
    if not _is_string_list(policy.get("forbidden_default_side_effects")):
        errors.append("forbidden_default_side_effects must be a list of strings")
    return errors


def _false_safety_flags() -> dict[str, bool]:
    return {flag: False for flag in SAFETY_FLAGS}


def _report_packet_sections(policy: dict[str, object], *, validation_passed: bool) -> dict[str, object]:
    if validation_passed:
        return {
            "manual_instruction_text": policy.get("manual_instruction_text", {}),
            "risk_notes": policy.get("risk_notes", []),
            "cleanup_notes": policy.get("cleanup_notes", []),
            "forbidden_default_side_effects": policy.get("forbidden_default_side_effects", []),
            "official_sources": policy.get("official_sources", []),
        }
    return {
        "manual_instruction_text": _trusted_manual_instruction_text(),
        "risk_notes": [],
        "cleanup_notes": [],
        "forbidden_default_side_effects": [],
        "official_sources": _trusted_official_sources(),
    }


def build_funasr_model_download_approval_packet(
    *,
    policy_path: Path = DEFAULT_POLICY_PATH,
) -> dict[str, object]:
    policy_path_errors = validate_policy_path(policy_path)
    if policy_path_errors:
        return _blocked_policy_path_report(policy_path_errors)

    policy = load_policy(policy_path)
    validation_errors = validate_policy(policy)
    validation_passed = not validation_errors
    packet_sections = _report_packet_sections(policy, validation_passed=validation_passed)

    return {
        "drv_id": policy.get("drv_id"),
        "policy_name": policy.get("policy_name"),
        "report_mode": "funasr_model_download_approval_packet_static_report",
        "policy_status": policy.get("policy_status"),
        "policy_validation_status": "passed" if validation_passed else "failed",
        "policy_validation_errors": validation_errors,
        "approval_packet_status": (
            "generated_for_manual_review" if validation_passed else "blocked_by_policy_validation"
        ),
        "approval_packet_mode": "manual_user_run_only",
        "execution_mode": "manual_user_run_only",
        "manual_instruction_text_status": "inert_text_only",
        "command_execution_status": "not_run",
        "external_command_execution_status": "not_run",
        "model_download_execution_status": "not_run",
        "model_provider": "ModelScope",
        "model_namespace": "iic",
        "model_id": MODEL_ID,
        "model_url": MODEL_URL,
        "expected_model_size_note": "about_840mb_observed_online_streaming_model",
        "target_cache_root_policy": "user_selected_or_modelscope_default_iic_runtime_cache",
        "required_model_files_after_download": list(REQUIRED_MODEL_FILES),
        "approval_blockers": list(REQUIRED_APPROVAL_TOKENS),
        "post_download_verification_order": list(POST_DOWNLOAD_VERIFICATION_ORDER),
        **packet_sections,
        **_false_safety_flags(),
    }


def _blocked_policy_path_report(policy_path_errors: list[str]) -> dict[str, object]:
    return {
        "drv_id": "DRV-019",
        "policy_name": "FunASR Model Download Approval Packet Policy",
        "report_mode": "funasr_model_download_approval_packet_static_report",
        "policy_status": "blocked_policy_path",
        "policy_read_status": "blocked",
        "policy_validation_status": "failed",
        "policy_validation_errors": policy_path_errors,
        "approval_packet_status": "blocked_by_policy_validation",
        "approval_packet_mode": "manual_user_run_only",
        "execution_mode": "manual_user_run_only",
        "manual_instruction_text_status": "inert_text_only",
        "command_execution_status": "not_run",
        "external_command_execution_status": "not_run",
        "model_download_execution_status": "not_run",
        "model_provider": "ModelScope",
        "model_namespace": "iic",
        "model_id": MODEL_ID,
        "model_url": MODEL_URL,
        "expected_model_size_note": "about_840mb_observed_online_streaming_model",
        "target_cache_root_policy": "user_selected_or_modelscope_default_iic_runtime_cache",
        "required_model_files_after_download": list(REQUIRED_MODEL_FILES),
        "approval_blockers": list(REQUIRED_APPROVAL_TOKENS),
        "post_download_verification_order": list(POST_DOWNLOAD_VERIFICATION_ORDER),
        "manual_instruction_text": _trusted_manual_instruction_text(),
        "risk_notes": [],
        "cleanup_notes": [],
        "forbidden_default_side_effects": [],
        "official_sources": _trusted_official_sources(),
        **_false_safety_flags(),
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--policy",
        type=Path,
        default=DEFAULT_POLICY_PATH,
        help="Path to the DRV-019 FunASR model download approval policy JSON.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, out: TextIO = sys.stdout) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    report = build_funasr_model_download_approval_packet(policy_path=args.policy)
    json.dump(report, out, ensure_ascii=False, indent=2)
    out.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
