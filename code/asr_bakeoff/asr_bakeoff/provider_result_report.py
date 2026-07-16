from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from meeting_copilot_web_mvp.asr_semantic_quality import evaluate_semantic_quality
from meeting_copilot_web_mvp.transcript_normalizer import normalize as normalize_transcript


SCHEMA_VERSION = "asr_provider_result_report.v1"
DEFAULT_CURRENT_PROVIDER = "sherpa_onnx_realtime"


def run_provider_result_report(
    *,
    input_paths: list[Path],
    output_path: Path,
    current_default_provider: str = DEFAULT_CURRENT_PROVIDER,
) -> dict[str, Any]:
    candidates = [_candidate_from_path(path) for path in input_paths]
    report = {
        "schema_version": SCHEMA_VERSION,
        "input_paths": [str(path) for path in input_paths],
        "summary": _summary(candidates),
        "default_provider_decision": _default_provider_decision(
            candidates,
            current_default_provider=current_default_provider,
        ),
        "candidates": candidates,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return report


def _candidate_from_path(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("events"), list):
        return _live_session_candidate(path, data)
    if isinstance(data, dict):
        return _provider_json_candidate(path, data)
    raise ValueError(f"unsupported provider result JSON shape: {path}")


def _live_session_candidate(path: Path, data: dict[str, Any]) -> dict[str, Any]:
    event_source = dict(data.get("event_source") or {})
    events = list(data.get("events") or [])
    final_events = [
        event
        for event in events
        if str(event.get("event_type") or "") in {"transcript_final", "final"}
    ]
    partial_events = [
        event
        for event in events
        if str(event.get("event_type") or "") in {"transcript_partial", "partial"}
    ]
    text = " ".join(
        str((event.get("payload") or {}).get("text") or event.get("text") or "").strip()
        for event in final_events
        if str((event.get("payload") or {}).get("text") or event.get("text") or "").strip()
    ).strip()
    normalized_text = normalize_transcript(text)
    audio = dict(data.get("audio") or {})
    semantic_quality = event_source.get("asr_semantic_quality")
    if not isinstance(semantic_quality, dict) or not semantic_quality:
        semantic_quality = evaluate_semantic_quality(normalized_text)
    return _candidate(
        source_path=path,
        input_kind="live_asr_session_events",
        provider=str(event_source.get("provider") or data.get("provider") or "unknown"),
        provider_mode=str(event_source.get("provider_mode") or data.get("provider_mode") or "unknown"),
        text=text,
        normalized_text=normalized_text,
        final_count=len(final_events),
        partial_count=len(partial_events),
        latency={},
        realtime_metrics=_realtime_metrics_from_events(events, timing_source=str(path)),
        resource_metrics=_resource_metrics_from_path(_sibling_resource_path(path)),
        semantic_quality=semantic_quality,
        saved_audio=bool(audio.get("saved")),
        acceptance_eligible=bool(event_source.get("acceptance_eligible", False)),
        acceptance_blockers=[str(item) for item in list(event_source.get("acceptance_blockers") or [])],
        cost_privacy={
            "remote_asr_called": _truthy_any(data, "remote_asr_called", default=False),
            "remote_llm_called": _truthy_any(data, "llm_called", "remote_llm_called", default=False),
            "model_download_performed": False,
            "raw_audio_uploaded": False,
        },
    )


def _provider_json_candidate(path: Path, data: dict[str, Any]) -> dict[str, Any]:
    raw = dict(data.get("raw") or {})
    text = str(data.get("text") or "")
    normalized_text = normalize_transcript(text)
    latency = {
        "latency_ms": _optional_int(data.get("latency_ms")),
        "audio_duration_seconds": _optional_float(data.get("audio_duration_seconds")),
        "rtf": _optional_float(data.get("rtf")),
    }
    provider_events_path = _sibling_events_path(path)
    provider_events = _load_event_list(provider_events_path)
    resource_metrics = _resource_metrics_from_path(_sibling_resource_path(path))
    semantic_quality = data.get("semantic_quality")
    if not isinstance(semantic_quality, dict) or not semantic_quality:
        semantic_quality = evaluate_semantic_quality(normalized_text)
    return _candidate(
        source_path=path,
        input_kind="provider_json",
        provider=str(raw.get("provider") or data.get("provider") or "unknown"),
        provider_mode=str(raw.get("provider_mode") or data.get("provider_mode") or "real"),
        text=text,
        normalized_text=normalized_text,
        final_count=int(raw.get("final_event_count") or len(data.get("segments") or []) or 0),
        partial_count=int(raw.get("partial_event_count") or 0),
        latency=latency,
        realtime_metrics=_realtime_metrics_from_events(
            provider_events,
            timing_source=str(provider_events_path) if provider_events_path and provider_events else "",
        ),
        resource_metrics=resource_metrics,
        semantic_quality=semantic_quality,
        saved_audio=False,
        acceptance_eligible=str(data.get("status") or "").lower() in {"ok", "success", ""},
        acceptance_blockers=[] if str(data.get("status") or "ok").lower() in {"ok", "success"} else ["provider_status_not_ok"],
        cost_privacy={
            "remote_asr_called": _truthy_any(raw, "remote_asr_called", default=False),
            "remote_llm_called": _truthy_any(raw, "llm_called", "remote_llm_called", default=False),
            "model_download_performed": str(raw.get("model_download_status") or "").lower() not in {
                "",
                "not_performed",
                "blocked_or_not_started",
            },
            "raw_audio_uploaded": _truthy_any(raw, "raw_audio_uploaded", default=False),
        },
    )


def _candidate(
    *,
    source_path: Path,
    input_kind: str,
    provider: str,
    provider_mode: str,
    text: str,
    normalized_text: str,
    final_count: int,
    partial_count: int,
    latency: dict[str, Any],
    realtime_metrics: dict[str, Any],
    resource_metrics: dict[str, Any],
    semantic_quality: dict[str, Any],
    saved_audio: bool,
    acceptance_eligible: bool,
    acceptance_blockers: list[str],
    cost_privacy: dict[str, bool],
) -> dict[str, Any]:
    text_quality = _text_quality(text, normalized_text)
    return {
        "source_path": str(source_path),
        "input_kind": input_kind,
        "provider": provider,
        "provider_mode": provider_mode,
        "text": text,
        "normalized_text": normalized_text,
        "final_count": final_count,
        "partial_count": partial_count,
        "latency": {key: value for key, value in latency.items() if value is not None},
        "realtime_metrics": realtime_metrics,
        "resource_metrics": resource_metrics,
        "semantic_quality": semantic_quality,
        "text_quality": text_quality,
        "saved_audio": saved_audio,
        "acceptance_eligible": acceptance_eligible,
        "acceptance_blockers": acceptance_blockers,
        "cost_privacy": cost_privacy,
        "risk_flags": _risk_flags(
            final_count=final_count,
            partial_count=partial_count,
            semantic_quality=semantic_quality,
            text_quality=text_quality,
            latency=latency,
            realtime_metrics=realtime_metrics,
            resource_metrics=resource_metrics,
            cost_privacy=cost_privacy,
        ),
    }


def _sibling_events_path(path: Path) -> Path | None:
    name = path.name
    candidates: list[Path] = []
    if name.endswith("-provider.json"):
        candidates.append(path.with_name(f"{name[:-len('-provider.json')]}-events.json"))
    if name.endswith(".provider.json"):
        candidates.append(path.with_name(f"{name[:-len('.provider.json')]}.events.json"))
    candidates.append(path.with_suffix(".events.json"))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _sibling_resource_path(path: Path) -> Path | None:
    name = path.name
    candidates: list[Path] = []
    if name.endswith("-provider.json"):
        candidates.append(path.with_name(f"{name[:-len('-provider.json')]}-resource.json"))
    if name.endswith(".provider.json"):
        candidates.append(path.with_name(f"{name[:-len('.provider.json')]}.resource.json"))
    candidates.append(path.with_suffix(".resource.json"))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _resource_metrics_from_path(path: Path | None) -> dict[str, Any]:
    empty = {
        "resource_source": "",
        "measured": False,
    }
    if path is None:
        return empty
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty
    if not isinstance(data, dict):
        return empty
    raw = data.get("resource_metrics") or data.get("resource") or data
    if not isinstance(raw, dict):
        return empty
    wall_seconds = _optional_float(_first_present(raw, "wall_seconds", "elapsed_seconds", "real_seconds"))
    user_cpu_seconds = _optional_float(_first_present(raw, "user_cpu_seconds", "user_seconds"))
    system_cpu_seconds = _optional_float(_first_present(raw, "system_cpu_seconds", "sys_seconds", "system_seconds"))
    max_rss_mb = _bytes_or_mb_to_mb(
        bytes_value=_first_present(raw, "max_rss_bytes", "maximum_resident_set_size_bytes"),
        mb_value=_first_present(raw, "max_rss_mb", "maximum_resident_set_size_mb"),
    )
    peak_footprint_mb = _bytes_or_mb_to_mb(
        bytes_value=_first_present(raw, "peak_memory_footprint_bytes"),
        mb_value=_first_present(raw, "peak_memory_footprint_mb"),
    )
    cpu_time_ratio = _optional_float(raw.get("cpu_time_ratio"))
    if cpu_time_ratio is None and wall_seconds and wall_seconds > 0:
        cpu_seconds = (user_cpu_seconds or 0.0) + (system_cpu_seconds or 0.0)
        cpu_time_ratio = round(cpu_seconds / wall_seconds, 6)
    result: dict[str, Any] = {
        "resource_source": str(path),
        "measured": True,
    }
    _put_if_not_none(result, "wall_seconds", wall_seconds)
    _put_if_not_none(result, "user_cpu_seconds", user_cpu_seconds)
    _put_if_not_none(result, "system_cpu_seconds", system_cpu_seconds)
    _put_if_not_none(result, "cpu_time_ratio", cpu_time_ratio)
    _put_if_not_none(result, "max_rss_mb", max_rss_mb)
    _put_if_not_none(result, "peak_memory_footprint_mb", peak_footprint_mb)
    return result


def _first_present(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data:
            return data.get(key)
    return None


def _bytes_or_mb_to_mb(*, bytes_value: Any, mb_value: Any) -> float | None:
    explicit_mb = _optional_float(mb_value)
    if explicit_mb is not None:
        return round(explicit_mb, 6)
    raw_bytes = _optional_float(bytes_value)
    if raw_bytes is None:
        return None
    return round(raw_bytes / 1024 / 1024, 6)


def _put_if_not_none(target: dict[str, Any], key: str, value: Any) -> None:
    if value is not None:
        target[key] = value


def _load_event_list(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return [dict(item) for item in data if isinstance(item, dict)]


def _realtime_metrics_from_events(events: list[Any], *, timing_source: str) -> dict[str, Any]:
    normalized_events = [_normalize_event_timing(event) for event in events if isinstance(event, dict)]
    partials = [event for event in normalized_events if event["event_type"] in {"partial", "transcript_partial"}]
    finals = [event for event in normalized_events if event["event_type"] in {"final", "transcript_final"}]
    partial_received = [event["received_at_ms"] for event in partials if event["received_at_ms"] is not None]
    final_received = [event["received_at_ms"] for event in finals if event["received_at_ms"] is not None]
    final_latencies = [
        max(0, int(event["received_at_ms"]) - int(event["end_ms"]))
        for event in finals
        if event["received_at_ms"] is not None and event["end_ms"] is not None
    ]
    final_intervals = [
        int(current) - int(previous)
        for previous, current in zip(final_received, final_received[1:])
        if current is not None and previous is not None
    ]
    return {
        "timing_source": timing_source,
        "partial_timing_count": len(partial_received),
        "final_timing_count": len(final_received),
        "first_partial_received_at_ms": min(partial_received) if partial_received else None,
        "first_final_received_at_ms": min(final_received) if final_received else None,
        "first_final_latency_ms": final_latencies[0] if final_latencies else None,
        "final_latency_ms": _int_summary(final_latencies),
        "final_interval_ms": _int_summary(final_intervals),
    }


def _normalize_event_timing(event: dict[str, Any]) -> dict[str, Any]:
    payload = dict(event.get("payload") or {})
    event_type = str(event.get("event_type") or "")
    start_ms = _optional_int(payload.get("start_ms") if payload else event.get("start_ms"))
    end_ms = _optional_int(payload.get("end_ms") if payload else event.get("end_ms"))
    received_at_ms = _optional_int(
        payload.get("received_at_ms")
        if payload and payload.get("received_at_ms") is not None
        else event.get("received_at_ms")
        if event.get("received_at_ms") is not None
        else event.get("at_ms")
    )
    return {
        "event_type": event_type,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "received_at_ms": received_at_ms,
    }


def _int_summary(values: list[int]) -> dict[str, int]:
    if not values:
        return {"count": 0, "p50_ms": 0, "p95_ms": 0, "max_ms": 0}
    sorted_values = sorted(int(value) for value in values)
    return {
        "count": len(sorted_values),
        "p50_ms": _nearest_rank(sorted_values, 50),
        "p95_ms": _nearest_rank(sorted_values, 95),
        "max_ms": max(sorted_values),
    }


def _nearest_rank(sorted_values: list[int], percentile: int) -> int:
    if not sorted_values:
        return 0
    index = max(0, min(len(sorted_values) - 1, (len(sorted_values) * percentile + 99) // 100 - 1))
    return sorted_values[index]


def _text_quality(text: str, normalized_text: str) -> dict[str, Any]:
    value = str(text or "")
    normalized = str(normalized_text or "")
    raw_contains_unk = "<unk>" in value
    normalized_contains_unk = "<unk>" in normalized
    return {
        "raw_char_count": len(value),
        "normalized_char_count": len(normalized),
        "raw_contains_unk": raw_contains_unk,
        "normalized_contains_unk": normalized_contains_unk,
        "contains_unk": normalized_contains_unk,
        "cjk_split_space_count": len(re.findall(r"[\u4e00-\u9fff]\s+[\u4e00-\u9fff]", normalized)),
        "technical_entity_hit_count": int(evaluate_semantic_quality(normalized).get("technical_entity_hit_count") or 0),
    }


def _risk_flags(
    *,
    final_count: int,
    partial_count: int,
    semantic_quality: dict[str, Any],
    text_quality: dict[str, Any],
    latency: dict[str, Any],
    realtime_metrics: dict[str, Any],
    resource_metrics: dict[str, Any],
    cost_privacy: dict[str, bool],
) -> list[str]:
    flags: list[str] = []
    if semantic_quality.get("status") != "passed":
        flags.append("semantic_quality_not_passed")
    if bool(text_quality.get("contains_unk")):
        flags.append("contains_unk")
    if int(text_quality.get("cjk_split_space_count") or 0) > 0:
        flags.append("contains_cjk_split_spaces")
    if final_count <= 0:
        flags.append("no_final_events")
    if partial_count <= 0:
        flags.append("no_partial_events")
    first_final_ms = realtime_metrics.get("first_final_received_at_ms")
    if isinstance(first_final_ms, int) and first_final_ms > 10_000:
        flags.append("first_final_after_10s")
    final_interval = dict(realtime_metrics.get("final_interval_ms") or {})
    if int(final_interval.get("max_ms") or 0) > 15_000:
        flags.append("final_interval_above_15s")
    final_latency = dict(realtime_metrics.get("final_latency_ms") or {})
    if int(final_latency.get("p95_ms") or 0) > 5_000:
        flags.append("final_latency_above_5s")
    rtf = latency.get("rtf")
    if isinstance(rtf, (int, float)) and float(rtf) > 0.5:
        flags.append("rtf_above_realtime_margin")
    max_rss_mb = resource_metrics.get("max_rss_mb")
    if isinstance(max_rss_mb, (int, float)) and float(max_rss_mb) > 2048:
        flags.append("max_rss_above_2gb")
    cpu_time_ratio = resource_metrics.get("cpu_time_ratio")
    if isinstance(cpu_time_ratio, (int, float)) and float(cpu_time_ratio) > 2.0:
        flags.append("cpu_time_ratio_above_2")
    if any(cost_privacy.values()):
        flags.append("cost_or_privacy_boundary_not_free_local")
    return flags


def _summary(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "candidate_count": len(candidates),
        "semantic_pass_count": sum(1 for item in candidates if item["semantic_quality"].get("status") == "passed"),
        "remote_asr_call_count": sum(1 for item in candidates if item["cost_privacy"].get("remote_asr_called")),
        "remote_llm_call_count": sum(1 for item in candidates if item["cost_privacy"].get("remote_llm_called")),
        "model_download_count": sum(1 for item in candidates if item["cost_privacy"].get("model_download_performed")),
        "saved_audio_candidate_count": sum(1 for item in candidates if item.get("saved_audio")),
        "resource_measured_count": sum(1 for item in candidates if item.get("resource_metrics", {}).get("measured")),
    }


def _default_provider_decision(
    candidates: list[dict[str, Any]],
    *,
    current_default_provider: str,
) -> dict[str, Any]:
    blockers = ["candidate_not_proven_on_natural_meeting"]
    provider_by_name = {str(item["provider"]): item for item in candidates}
    for item in candidates:
        if item["provider"] == current_default_provider:
            continue
        risk_flags = set(item.get("risk_flags") or [])
        if risk_flags & {
            "semantic_quality_not_passed",
            "contains_unk",
            "contains_cjk_split_spaces",
            "rtf_above_realtime_margin",
            "max_rss_above_2gb",
            "cpu_time_ratio_above_2",
        }:
            blockers.append(f"{item['provider']}_has_quality_or_latency_risk")
    if not provider_by_name.get(current_default_provider):
        blockers.append("current_default_provider_not_in_report")
    return {
        "current_default_provider": current_default_provider,
        "recommended_action": "keep_current_default",
        "replacement_allowed": False,
        "blockers": _dedupe(blockers),
        "candidate_ranking": _candidate_ranking(candidates),
    }


def _candidate_ranking(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = sorted(candidates, key=_candidate_sort_key)
    return [
        {
            "provider": item["provider"],
            "rank": index,
            "risk_flags": list(item.get("risk_flags") or []),
            "semantic_status": item["semantic_quality"].get("status"),
            "final_count": item["final_count"],
            "partial_count": item["partial_count"],
            "rtf": item.get("latency", {}).get("rtf"),
            "max_rss_mb": item.get("resource_metrics", {}).get("max_rss_mb"),
        }
        for index, item in enumerate(ranked, start=1)
    ]


def _candidate_sort_key(candidate: dict[str, Any]) -> tuple[int, int, float, int]:
    risk_count = len(candidate.get("risk_flags") or [])
    semantic_penalty = 0 if candidate["semantic_quality"].get("status") == "passed" else 1
    rtf = candidate.get("latency", {}).get("rtf")
    rtf_value = float(rtf) if isinstance(rtf, (int, float)) else 0.0
    return (semantic_penalty, risk_count, rtf_value, -int(candidate.get("final_count") or 0))


def _truthy_any(data: dict[str, Any], *keys: str, default: bool) -> bool:
    for key in keys:
        if key in data:
            return bool(data.get(key))
    return default


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _dedupe(values: list[str]) -> list[str]:
    deduped: list[str] = []
    for value in values:
        if value not in deduped:
            deduped.append(value)
    return deduped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize ASR provider result JSON files.")
    parser.add_argument("--input", dest="inputs", required=True, type=Path, action="append")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--current-default-provider", default=DEFAULT_CURRENT_PROVIDER)
    args = parser.parse_args(argv)

    report = run_provider_result_report(
        input_paths=list(args.inputs),
        output_path=args.output,
        current_default_provider=args.current_default_provider,
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
