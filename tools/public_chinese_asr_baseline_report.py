#!/usr/bin/env python3
"""Build a bounded public Chinese ASR baseline report.

The downloaded public audio is a reproducible ASR yardstick, not product data
and not a reason to keep collecting more samples. This tool turns provider JSON
outputs into evidence: CER for samples with public references, qualitative notes
for meeting samples without references, RTF, and cost/privacy safety flags.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


REPORT_KIND = "public_chinese_asr_baseline"
PURPOSE = "reproducible_chinese_asr_quality_baseline_not_product_feature"

OSR_REFERENCES = {
    "OSR_cn_000_0072_8k": (
        "院子门口不远处就是一个地铁站。"
        "这是一个美丽而神奇的景象。"
        "树上长满了又大又甜的桃子。"
        "海豚和鲸鱼的表演是很好看的节目。"
        "邮局门前的人行道上有一个蓝色的邮箱。"
    ),
    "OSR_cn_000_0075_8k": (
        "天文望远镜可以用来观察天空。"
        "她到过很多地方观光旅游。"
        "山间的小道蜿蜒曲折。"
        "春天来了，山上开满了樱花。"
        "下雪以后，田野里白皑皑的一片。"
    ),
    "OSR_cn_000_0074_8k": (
        "夏天，有很多小朋友在沙滩上玩耍。"
        "校园依山环湖，风景如画。"
        "冬天，北风呼啸，雪花飞舞。"
        "忽然一道闪电，把天空和大地都照亮了。"
        "古城门附近是一个休闲散心的好去处。"
    ),
    "OSR_cn_000_0073_8k": (
        "她用画笔为自己画了一幅美丽的人生蓝图。"
        "一只白鹭站在河畔的浅水里。"
        "宿舍楼旁边的十字路口有一个公共汽车站。"
        "夏日的夕阳很美丽，尤其是夏日大平原的夕阳。"
        "那个年代已经一去不复返了。"
    ),
}

KNOWN_NEAR_MISSES = (
    ("尤局", "邮局", "common_mandarin_word_confusion"),
    ("油箱", "邮箱", "common_mandarin_word_confusion"),
    ("他用画笔", "她用画笔", "pronoun_confusion"),
    ("狼图", "蓝图", "common_mandarin_word_confusion"),
    ("衣裳惶虎", "依山环湖", "phrase_level_acoustic_confusion"),
    ("上心", "散心", "common_mandarin_word_confusion"),
    ("三间的小道", "山间的小道", "common_mandarin_word_confusion"),
    ("窜天来了", "春天来了", "common_mandarin_word_confusion"),
    ("白皑矮", "白皑皑", "common_mandarin_word_confusion"),
)


def build_report_from_provider_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    items = [_build_item(record) for record in records]
    summary = _build_summary(items)
    return {
        "report_kind": REPORT_KIND,
        "purpose": PURPOSE,
        "why_public_audio_was_used": [
            "create_reproducible_chinese_asr_baseline_when_real_mic_audio_is_not_stable_enough",
            "measure_actual_chinese_recognition_errors_instead_of_guessing",
            "compare_optimizations_on_the_same_inputs_before_touching_product_release_criteria",
        ],
        "scope_boundary": [
            "public_audio_is_not_a_product_feature",
            "do_not_download_more_audio_until_this_baseline_has_a_fix_and_rerun",
            "do_not_hardcode_public_sample_sentences_into_product_normalization_rules",
            "meeting_like_samples_without_reference_transcripts_are_qualitative_only",
        ],
        "items": items,
        "summary": summary,
        "recommendations": _recommendations(summary),
        "remote_asr_call_count": 0,
        "llm_call_count": 0,
        "raw_audio_uploaded": False,
        "safe_to_read_user_audio": False,
        "safe_to_read_configs_local": False,
        "safe_to_commit_raw_audio": False,
    }


def build_report_from_provider_dir(provider_dir: Path) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for path in sorted(provider_dir.glob("*.provider.json")):
        audio_id = _audio_id_from_provider_path(path)
        records.append(
            {
                "audio_id": audio_id,
                "source_id": _source_id_for_audio(audio_id),
                "provider_result": json.loads(path.read_text(encoding="utf-8")),
                "provider_json": _safe_path(path),
            }
        )
    return build_report_from_provider_records(records)


def _build_item(record: dict[str, Any]) -> dict[str, Any]:
    audio_id = str(record.get("audio_id") or "")
    source_id = str(record.get("source_id") or _source_id_for_audio(audio_id))
    provider_result = record.get("provider_result") if isinstance(record.get("provider_result"), dict) else {}
    status = provider_result.get("status")
    text = provider_result.get("text")

    item: dict[str, Any] = {
        "audio_id": audio_id,
        "source_id": source_id,
        "status": str(status) if isinstance(status, str) else "invalid_missing_status",
        "reference_status": "unavailable_qualitative_meeting_sample",
        "text": text if isinstance(text, str) else "",
        "latency_ms": _number_or_zero(provider_result.get("latency_ms")),
        "audio_duration_seconds": _number_or_zero(provider_result.get("audio_duration_seconds")),
        "rtf": _number_or_zero(provider_result.get("rtf")),
        "observed_near_misses": [],
    }
    if "provider_json" in record:
        item["provider_json"] = str(record["provider_json"])

    if not isinstance(status, str):
        return item
    if not isinstance(text, str) or not text:
        item["status"] = "invalid_missing_text"
        return item

    reference = OSR_REFERENCES.get(audio_id)
    if reference is None:
        return item

    reference_norm = _normalize_for_cer(reference)
    hypothesis_norm = _normalize_for_cer(text)
    distance = _levenshtein_distance(reference_norm, hypothesis_norm)
    item.update(
        {
            "reference_status": "available",
            "reference_char_count": len(reference_norm),
            "edit_distance": distance,
            "cer": round(distance / len(reference_norm), 6) if reference_norm else 0,
            "observed_near_misses": _observed_near_misses(text, reference),
        }
    )
    return item


def _build_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    valid_items = [item for item in items if item.get("status") == "ok"]
    referenced = [item for item in valid_items if item.get("reference_status") == "available"]
    qualitative = [item for item in valid_items if item.get("reference_status") != "available"]
    invalid_count = len(items) - len(valid_items)
    total_ref_chars = sum(int(item.get("reference_char_count") or 0) for item in referenced)
    total_edits = sum(int(item.get("edit_distance") or 0) for item in referenced)
    rtf_values = [float(item.get("rtf") or 0) for item in valid_items]
    weighted_cer = round(total_edits / total_ref_chars, 6) if total_ref_chars else None
    avg_cer = round(sum(float(item.get("cer") or 0) for item in referenced) / len(referenced), 6) if referenced else None
    max_rtf = round(max(rtf_values), 6) if rtf_values else 0
    avg_rtf = round(sum(rtf_values) / len(rtf_values), 6) if rtf_values else 0

    summary = {
        "item_count": len(items),
        "valid_item_count": len(valid_items),
        "invalid_item_count": invalid_count,
        "referenced_item_count": len(referenced),
        "qualitative_only_item_count": len(qualitative),
        "weighted_cer": weighted_cer,
        "avg_cer": avg_cer,
        "max_rtf": max_rtf,
        "avg_rtf": avg_rtf,
    }
    summary["release_gate_status"] = _release_gate_status(
        invalid_count=invalid_count,
        weighted_cer=weighted_cer,
        max_rtf=max_rtf,
    )
    return summary


def _release_gate_status(*, invalid_count: int, weighted_cer: float | None, max_rtf: float) -> str:
    if invalid_count:
        return "invalid_baseline_inputs"
    if weighted_cer is None:
        return "needs_reference_samples_before_release_gate"
    if weighted_cer > 0.02 or max_rtf > 1.0:
        return "needs_asr_optimization_before_release"
    return "baseline_passed_for_current_public_samples"


def _recommendations(summary: dict[str, Any]) -> list[str]:
    recommendations = ["stop_downloading_more_audio_until_current_baseline_has_a_fix_and_rerun"]
    if summary.get("release_gate_status") == "needs_asr_optimization_before_release":
        recommendations.extend(
            [
                "optimize_streaming_runtime_or_provider_selection_before_real_meeting_release_gate",
                "use_llm_or_punctuation_repair_for_readability_after_raw_asr_is_captured",
                "keep_product_corrections_domain_scoped_instead_of_overfitting_public_sample_sentences",
            ]
        )
    elif summary.get("release_gate_status") == "baseline_passed_for_current_public_samples":
        recommendations.append("rerun_real_mic_mainline_before_claiming_product_readiness")
    else:
        recommendations.append("fix_report_inputs_before_interpreting_quality")
    return recommendations


def _observed_near_misses(text: str, reference: str) -> list[dict[str, str]]:
    misses: list[dict[str, str]] = []
    for observed, expected, risk in KNOWN_NEAR_MISSES:
        if observed in text and expected in reference:
            misses.append({"observed": observed, "expected": expected, "risk": risk})
    return misses


def _normalize_for_cer(value: str) -> str:
    return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9%]", "", value)


def _levenshtein_distance(left: str, right: str) -> int:
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)
    previous = list(range(len(right) + 1))
    for left_index, left_char in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_char in enumerate(right, start=1):
            insert_cost = current[right_index - 1] + 1
            delete_cost = previous[right_index] + 1
            replace_cost = previous[right_index - 1] + (left_char != right_char)
            current.append(min(insert_cost, delete_cost, replace_cost))
        previous = current
    return previous[-1]


def _number_or_zero(value: Any) -> float | int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value
    return 0


def _audio_id_from_provider_path(path: Path) -> str:
    name = path.name
    suffix = ".funasr.provider.json"
    return name[: -len(suffix)] if name.endswith(suffix) else path.stem


def _source_id_for_audio(audio_id: str) -> str:
    if audio_id.startswith("OSR_cn_"):
        return "osr_mandarin"
    if "magichub" in audio_id:
        return "magichub_web_meeting_sample"
    return "unknown_public_chinese_audio"


def _safe_path(path: Path) -> str:
    try:
        return path.relative_to(Path.cwd()).as_posix()
    except ValueError:
        return path.as_posix()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Build a public Chinese ASR baseline report.")
    parser.add_argument("--provider-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    report = build_report_from_provider_dir(args.provider_dir)
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)


if __name__ == "__main__":
    main()
