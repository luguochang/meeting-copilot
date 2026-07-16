from __future__ import annotations

from copy import deepcopy
from difflib import SequenceMatcher
import re
from typing import Any, Iterable

from meeting_copilot_web_mvp.transcript_normalizer import normalize as normalize_asr_terms


POLICY_VERSION = "realtime-transcript-correction.v1"
# Keep the paid L2 call bounded while making short meetings visibly useful.
# A confirmed final can be corrected after 15 seconds even when it is shorter
# than the character threshold; stop/finalize still uses force=True.
MIN_BATCH_CHARS = 80
MIN_INTERVAL_MS = 15_000
MAX_BATCH_CHARS = 2_000
MIN_LENGTH_RATIO = 0.65
MAX_LENGTH_RATIO = 1.40
MIN_SIMILARITY = 0.65
RESERVATION_LEASE_MS = 60_000
MAX_RESERVATION_RETRIES = 1

_BATCH_START_RE = re.compile(r"^<<<MC_SEGMENT:(\d{4}):([^>]+)>>>$")
_BATCH_END_RE = re.compile(r"^<<<MC_END:(\d{4})>>>$")
_BATCH_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_AUDIT_VALUE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")


def eligible_final_batch(
    record: dict[str, Any],
    *,
    force: bool = False,
    now_ms: int | None = None,
) -> dict[str, Any]:
    events = list(record.get("events") or [])
    revised_segment_ids = _revised_segment_ids(events)
    correction_status = dict(record.get("realtime_transcript_correction") or {})
    processed_segment_ids = set(correction_status.get("processed_segment_ids") or [])
    in_flight_segment_ids = set(correction_status.get("in_flight_segment_ids") or [])
    failed_segment_ids = set(correction_status.get("failed_segment_ids") or [])
    skipped_segment_ids = set(correction_status.get("skipped_segment_ids") or [])
    failure_counts = {
        str(segment_id): max(0, int(count or 0))
        for segment_id, count in dict(correction_status.get("failure_counts") or {}).items()
    }

    candidates: list[dict[str, Any]] = []
    oversized_segment_ids: list[str] = []
    for event in events:
        if event.get("event_type") not in {"final", "transcript_final"}:
            continue
        segment_id = _event_segment_id(event)
        text = _event_text(event)
        if not segment_id or not text:
            continue
        if segment_id in revised_segment_ids or segment_id in processed_segment_ids or segment_id in in_flight_segment_ids or segment_id in skipped_segment_ids:
            continue
        failure_count = failure_counts.get(segment_id, 0)
        if (not force and (segment_id in failed_segment_ids or failure_count >= 1)) or (force and failure_count >= 2):
            continue
        if len(text) > MAX_BATCH_CHARS:
            oversized_segment_ids.append(segment_id)
            continue
        candidates.append(event)

    candidates.sort(key=lambda event: (int(event.get("at_ms") or 0), str(event.get("id") or "")))
    selected: list[dict[str, Any]] = []
    total_chars = 0
    for event in candidates:
        text = _event_text(event)
        if selected and total_chars + len(text) > MAX_BATCH_CHARS:
            break
        selected.append(event)
        total_chars += len(text)
        if not force and total_chars >= MIN_BATCH_CHARS:
            break

    if not selected:
        return _batch_result(
            False,
            "oversized_final_skipped" if oversized_segment_ids else "no_unrevised_final",
            [],
            total_chars=0,
            elapsed_ms=0,
            oversized_segment_ids=oversized_segment_ids,
        )

    if now_ms is not None:
        current_ms = max(0, int(now_ms))
        interval_start_ms = int(
            correction_status.get("last_batch_wall_clock_ms")
            or correction_status.get("waiting_since_epoch_ms")
            or current_ms
        )
        elapsed_ms = max(0, current_ms - interval_start_ms)
    else:
        latest_event_at_ms = max((int(event.get("at_ms") or 0) for event in events), default=0)
        first_final_at_ms = int(selected[0].get("at_ms") or 0)
        last_batch_at_ms = int(correction_status.get("last_batch_at_ms") or first_final_at_ms)
        interval_start_ms = max(first_final_at_ms, last_batch_at_ms)
        elapsed_ms = max(0, latest_event_at_ms - interval_start_ms)

    if force:
        return _batch_result(True, "forced", selected, total_chars=total_chars, elapsed_ms=elapsed_ms, oversized_segment_ids=oversized_segment_ids)
    if total_chars >= MIN_BATCH_CHARS:
        return _batch_result(True, "min_batch_chars_reached", selected, total_chars=total_chars, elapsed_ms=elapsed_ms, oversized_segment_ids=oversized_segment_ids)
    if elapsed_ms >= MIN_INTERVAL_MS:
        return _batch_result(True, "min_interval_reached", selected, total_chars=total_chars, elapsed_ms=elapsed_ms, oversized_segment_ids=oversized_segment_ids)
    return _batch_result(False, "batch_gate_closed", selected, total_chars=total_chars, elapsed_ms=elapsed_ms, oversized_segment_ids=oversized_segment_ids)


def reservation_action(record: dict[str, Any], *, now_ms: int) -> dict[str, Any]:
    correction_status = dict(record.get("realtime_transcript_correction") or {})
    reservation = dict(correction_status.get("reservation") or {})
    if not reservation or reservation.get("status") != "reserved":
        return {"action": "none", "reservation": reservation}
    lease_expires_at_ms = max(0, int(reservation.get("lease_expires_at_ms") or 0))
    if max(0, int(now_ms)) < lease_expires_at_ms:
        return {"action": "in_flight", "reservation": reservation}
    retry_count = max(0, int(reservation.get("retry_count") or 0))
    if retry_count < MAX_RESERVATION_RETRIES:
        return {"action": "retry", "reservation": reservation}
    return {"action": "expired_terminal", "reservation": reservation}


def final_events_for_segment_ids(
    record: dict[str, Any],
    segment_ids: list[str],
) -> list[dict[str, Any]]:
    requested = [str(segment_id).strip() for segment_id in segment_ids if str(segment_id).strip()]
    by_segment_id: dict[str, dict[str, Any]] = {}
    for event in list(record.get("events") or []):
        if event.get("event_type") not in {"final", "transcript_final"}:
            continue
        segment_id = _event_segment_id(event)
        if segment_id and segment_id not in by_segment_id:
            by_segment_id[segment_id] = event
    return [by_segment_id[segment_id] for segment_id in requested if segment_id in by_segment_id]


def begin_reservation(
    record: dict[str, Any],
    *,
    batch_id: str,
    segment_ids: list[str],
    now_ms: int,
    retry_count: int = 0,
) -> dict[str, Any]:
    safe_batch_id = _safe_batch_id(batch_id)
    normalized_segment_ids = sorted({str(segment_id).strip() for segment_id in segment_ids if str(segment_id).strip()})
    started_at_ms = max(0, int(now_ms))
    updated = dict(record)
    correction_status = dict(record.get("realtime_transcript_correction") or {})
    correction_status.update({
        "policy_version": POLICY_VERSION,
        "status": "reserved",
        "reservation": {
            "batch_id": safe_batch_id,
            "segment_ids": normalized_segment_ids,
            "started_at_ms": started_at_ms,
            "lease_expires_at_ms": started_at_ms + RESERVATION_LEASE_MS,
            "retry_count": max(0, int(retry_count)),
            "status": "reserved",
        },
    })
    updated["realtime_transcript_correction"] = correction_status
    return updated


def commit_batch_audit(
    record: dict[str, Any],
    *,
    batch_id: str,
    status: str,
    completed_at_ms: int,
    usage: dict[str, Any],
    error_code: str | None = None,
    provider: str = "not_configured",
    model: str = "not_called",
    purpose: str = "realtime_transcript_correction",
    degraded: bool = False,
    fallback: bool = True,
    retry: bool = False,
) -> dict[str, Any]:
    safe_batch_id = _safe_batch_id(batch_id)
    updated = dict(record)
    correction_status = dict(record.get("realtime_transcript_correction") or {})
    reservation = dict(correction_status.get("reservation") or {})
    if str(reservation.get("batch_id") or "") != safe_batch_id:
        raise ValueError("realtime correction reservation batch_id mismatch")
    completed = max(0, int(completed_at_ms))
    committed_reservation = {
        **reservation,
        "status": str(status),
        "completed_at_ms": completed,
    }
    audit = {
        "batch_id": safe_batch_id,
        "segment_ids": list(committed_reservation.get("segment_ids") or []),
        "started_at_ms": max(0, int(committed_reservation.get("started_at_ms") or 0)),
        "completed_at_ms": completed,
        "retry_count": max(0, int(committed_reservation.get("retry_count") or 0)),
        "status": str(status),
        "usage": _safe_usage(usage),
        "provider": _safe_audit_value(provider, fallback="redacted_provider"),
        "model": _safe_audit_value(model, fallback="redacted_model"),
        "purpose": _safe_audit_value(purpose, fallback="realtime_transcript_correction"),
        "degraded": bool(degraded),
        "fallback": bool(fallback),
        "retry": bool(retry),
    }
    if error_code:
        audit["error_code"] = _safe_audit_value(error_code, fallback="provider_failed")
    audits = [
        dict(existing)
        for existing in list(correction_status.get("batch_audits") or [])
        if str((existing or {}).get("batch_id") or "") != safe_batch_id
    ]
    audits.append(audit)
    correction_status.update({
        "policy_version": POLICY_VERSION,
        "reservation": committed_reservation,
        "batch_audits": audits,
        "last_batch_id": safe_batch_id,
    })
    updated["realtime_transcript_correction"] = correction_status
    return updated


def build_revision_event(
    *,
    session_id: str,
    final_event: dict[str, Any],
    corrected_text: str,
    source: str,
    usage: dict[str, Any],
    batch_id: str | None = None,
) -> dict[str, Any] | None:
    del session_id  # The deterministic event id is scoped by its owning session record.
    original_text = _event_text(final_event)
    corrected = str(corrected_text or "").strip()
    segment_id = _event_segment_id(final_event)
    if not segment_id or not _correction_is_safe(original_text, corrected):
        return None

    payload = dict(final_event.get("payload") or {})
    start_ms = int(payload.get("start_ms") or 0)
    end_ms = int(payload.get("end_ms") or final_event.get("at_ms") or start_ms)
    original_evidence = _original_evidence(payload, segment_id=segment_id, text=original_text, start_ms=start_ms, end_ms=end_ms)
    revision_segment_id = f"{segment_id}:rtc-v1"
    evidence_id = f"asr_ev_{revision_segment_id}"
    active_evidence = {
        "id": evidence_id,
        "segment_id": revision_segment_id,
        "quote": corrected,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "status": "active",
        "revision_of": str(original_evidence.get("id") or f"asr_ev_{segment_id}"),
    }
    superseded_evidence = dict(original_evidence)
    superseded_evidence["status"] = "superseded"
    superseded_evidence["replaced_by"] = evidence_id

    correction_metadata = {
        "policy_version": POLICY_VERSION,
        "source": str(source or "unknown"),
    }
    if batch_id is not None:
        correction_metadata["batch_id"] = _safe_batch_id(batch_id)
    else:
        correction_metadata["usage"] = _safe_usage(usage)

    return {
        "id": f"transcript_revision:{segment_id}:rtc-v1",
        "event_type": "transcript_revision",
        "at_ms": int(final_event.get("at_ms") or end_ms),
        "source": str(final_event.get("source") or "live_asr_stream"),
        "trace_kind": str(final_event.get("trace_kind") or "live_event"),
        "payload": {
            "segment_id": revision_segment_id,
            "revision_of": segment_id,
            "supersedes_segment_id": segment_id,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "confidence": payload.get("confidence"),
            "text": corrected,
            "normalized_text": corrected,
            "original_text": original_text,
            "is_final": True,
            "evidence_spans": [active_evidence],
            "superseded_evidence_spans": [superseded_evidence],
            "correction": correction_metadata,
        },
    }


def apply_revision_events(
    record: dict[str, Any],
    revisions: list[dict[str, Any]],
    *,
    status: dict[str, Any],
) -> dict[str, Any]:
    updated = dict(record)
    events = list(record.get("events") or [])
    event_ids = {str(event.get("id") or "") for event in events}
    for revision in revisions:
        event_id = str(revision.get("id") or "")
        if not event_id or event_id in event_ids:
            continue
        appended_revision = deepcopy(revision)
        appended_revision["sequence"] = len(events) + 1
        events.append(appended_revision)
        event_ids.add(event_id)

    correction_status = dict(record.get("realtime_transcript_correction") or {})
    correction_status.update(dict(status or {}))
    correction_status["policy_version"] = POLICY_VERSION
    revised_segment_ids = set(correction_status.get("revised_segment_ids") or [])
    processed_segment_ids = set(correction_status.get("processed_segment_ids") or [])
    for revision in events:
        if revision.get("event_type") != "transcript_revision":
            continue
        payload = revision.get("payload") or {}
        if (payload.get("correction") or {}).get("policy_version") != POLICY_VERSION:
            continue
        target = str(payload.get("supersedes_segment_id") or payload.get("revision_of") or "")
        if target:
            revised_segment_ids.add(target)
            processed_segment_ids.add(target)
    correction_status["revised_segment_ids"] = sorted(revised_segment_ids)
    correction_status["processed_segment_ids"] = sorted(processed_segment_ids)
    updated["events"] = events
    updated["realtime_transcript_correction"] = correction_status
    return updated


def effective_final_events(record: dict[str, Any]) -> list[dict[str, Any]]:
    projected: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for event in list(record.get("events") or []):
        event_type = str(event.get("event_type") or "")
        if event_type in {"final", "transcript_final"}:
            segment_id = _event_segment_id(event)
            if segment_id and segment_id not in projected:
                order.append(segment_id)
                projected[segment_id] = event
        elif event_type == "transcript_revision":
            payload = event.get("payload") or {}
            target = str(payload.get("supersedes_segment_id") or payload.get("revision_of") or "")
            if target and target in projected:
                projected[target] = event
    return [deepcopy(projected[segment_id]) for segment_id in order]


def encode_indexed_batch(final_events: Iterable[dict[str, Any]]) -> str:
    blocks: list[str] = []
    for index, event in enumerate(final_events, start=1):
        segment_id = _event_segment_id(event)
        if not segment_id or ">>>" in segment_id or "\n" in segment_id:
            raise ValueError("unsafe segment_id for correction batch")
        blocks.extend([
            f"<<<MC_SEGMENT:{index:04d}:{segment_id}>>>",
            _event_text(event),
            f"<<<MC_END:{index:04d}>>>",
        ])
    return "\n".join(blocks)


def decode_indexed_batch(corrected_batch: str, final_events: Iterable[dict[str, Any]]) -> list[str] | None:
    expected = [(_event_segment_id(event), index) for index, event in enumerate(final_events, start=1)]
    lines = str(corrected_batch or "").splitlines()
    cursor = 0
    decoded: list[str] = []
    for segment_id, index in expected:
        if cursor >= len(lines):
            return None
        start_match = _BATCH_START_RE.fullmatch(lines[cursor].strip())
        if not start_match or int(start_match.group(1)) != index or start_match.group(2) != segment_id:
            return None
        cursor += 1
        content: list[str] = []
        while cursor < len(lines) and not _BATCH_END_RE.fullmatch(lines[cursor].strip()):
            if _BATCH_START_RE.fullmatch(lines[cursor].strip()):
                return None
            content.append(lines[cursor])
            cursor += 1
        if cursor >= len(lines):
            return None
        end_match = _BATCH_END_RE.fullmatch(lines[cursor].strip())
        if not end_match or int(end_match.group(1)) != index:
            return None
        cursor += 1
        decoded.append("\n".join(content).strip())
    if any(line.strip() for line in lines[cursor:]):
        return None
    return decoded


def _batch_result(
    eligible: bool,
    reason: str,
    events: list[dict[str, Any]],
    *,
    total_chars: int,
    elapsed_ms: int,
    oversized_segment_ids: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "eligible": eligible,
        "reason": reason,
        "final_events": events,
        "segment_ids": [_event_segment_id(event) for event in events],
        "total_chars": total_chars,
        "elapsed_ms": elapsed_ms,
        "retry_after_ms": 0 if eligible else max(0, MIN_INTERVAL_MS - elapsed_ms),
        "oversized_segment_ids": list(oversized_segment_ids or []),
        "policy_version": POLICY_VERSION,
    }


def _event_segment_id(event: dict[str, Any]) -> str:
    payload = event.get("payload") or {}
    return str(payload.get("segment_id") or event.get("segment_id") or "").strip()


def _event_text(event: dict[str, Any]) -> str:
    payload = event.get("payload") or {}
    return str(
        payload.get("normalized_text")
        or payload.get("text")
        or event.get("normalized_text")
        or event.get("text")
        or ""
    ).strip()


def _revised_segment_ids(events: Iterable[dict[str, Any]]) -> set[str]:
    revised: set[str] = set()
    for event in events:
        if event.get("event_type") != "transcript_revision":
            continue
        payload = event.get("payload") or {}
        target = str(payload.get("supersedes_segment_id") or payload.get("revision_of") or "").strip()
        if target:
            revised.add(target)
    return revised


def _correction_is_safe(original: str, corrected: str) -> bool:
    original = str(original or "").strip()
    corrected = str(corrected or "").strip()
    if not original or not corrected or original == corrected:
        return False
    if "<<<MC_" in corrected:
        return False
    normalized_original = _similarity_text(original)
    normalized_corrected = _similarity_text(corrected)
    if not normalized_original or not normalized_corrected or normalized_original == normalized_corrected:
        return False
    length_ratio = len(normalized_corrected) / max(len(normalized_original), 1)
    if not MIN_LENGTH_RATIO <= length_ratio <= MAX_LENGTH_RATIO:
        return False
    if _fact_signature(original) != _fact_signature(corrected):
        return False
    similarity = SequenceMatcher(None, normalized_original, normalized_corrected).ratio()
    return similarity >= MIN_SIMILARITY


_CHINESE_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CHINESE_UNITS = {"十": 10, "百": 100, "千": 1_000, "万": 10_000, "亿": 100_000_000}
_CHINESE_NUMBER_CHARS = "零〇一二两三四五六七八九十百千万亿点"
_NUMBER_FACT_RE = re.compile(
    rf"百分之(?P<percent_cn>[{_CHINESE_NUMBER_CHARS}]+)"
    rf"|(?P<percent_ar>\d+(?:\.\d+)?)\s*(?:%|％|趴)"
    rf"|(?P<percent_pai>[{_CHINESE_NUMBER_CHARS}]+)趴"
    r"|(?:周|星期)(?P<weekday>[一二三四五六日天])"
    rf"|(?P<cn_unit>[{_CHINESE_NUMBER_CHARS}]+)\s*(?P<cn_unit_name>毫秒|秒|分钟|分|小时|天|周|月|年|元)"
    r"|(?P<ar_unit>\d+(?:\.\d+)?)\s*(?P<ar_unit_name>毫秒|秒|分钟|分|小时|天|周|月|年|元)"
)
_OWNER_RE = re.compile(
    r"(?:负责人|责任人|执行人|跟进人|主责|owner)\s*(?:是|为|:|：)?\s*([^，。；;！？,\n]{1,32})",
    re.IGNORECASE,
)
_IMPLICIT_OWNER_RE = re.compile(
    r"([\u4e00-\u9fffA-Za-z0-9_]{2,32})\s*(?:负责|主责|owner)",
    re.IGNORECASE,
)
_DATE_FACT_RE = re.compile(
    rf"(?P<month>[{_CHINESE_NUMBER_CHARS}]+)月(?P<day>[{_CHINESE_NUMBER_CHARS}]+)日"
)
_TECH_ENTITY_RE = re.compile(
    r"[\u4e00-\u9fffA-Za-z0-9._/-]{1,24}(?:服务|系统|模块|接口|项目|客户|公司|团队|仓库|数据库|集群)",
    re.IGNORECASE,
)
_DECISION_FACT_TERMS = (
    "通过", "拒绝", "批准", "驳回", "同意", "否决", "确定", "取消", "暂停", "继续", "上线", "下线", "回滚", "禁止", "允许",
    "不需要", "无需", "需要", "必须", "可以", "不能", "不允许", "未通过", "未完成",
)


def _fact_signature(text: str) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    source = str(text or "")
    numbers: list[str] = []
    for match in _NUMBER_FACT_RE.finditer(source):
        groups = match.groupdict()
        if groups.get("percent_cn"):
            numbers.append(f"percent:{_canonical_number(groups['percent_cn'])}")
        elif groups.get("percent_ar"):
            numbers.append(f"percent:{_canonical_number(groups['percent_ar'])}")
        elif groups.get("percent_pai"):
            numbers.append(f"percent:{_canonical_number(groups['percent_pai'])}")
        elif groups.get("weekday"):
            weekday = "7" if groups["weekday"] in {"日", "天"} else str(_CHINESE_DIGITS[groups["weekday"]])
            numbers.append(f"weekday:{weekday}")
        elif groups.get("cn_unit"):
            numbers.append(f"{groups['cn_unit_name']}:{_canonical_number(groups['cn_unit'])}")
        elif groups.get("ar_unit"):
            numbers.append(f"{groups['ar_unit_name']}:{_canonical_number(groups['ar_unit'])}")
    owners = [
        _similarity_text(match.group(1))
        for match in _OWNER_RE.finditer(source)
        if _similarity_text(match.group(1))
    ]
    owners.extend(
        _similarity_text(match.group(1))
        for match in _IMPLICIT_OWNER_RE.finditer(source)
        if _similarity_text(match.group(1))
    )
    dates = [
        f"date:{_canonical_number(match.group('month'))}-{_canonical_number(match.group('day'))}"
        for match in _DATE_FACT_RE.finditer(source)
    ]
    entities = [_similarity_text(match.group(0)) for match in _TECH_ENTITY_RE.finditer(source)]
    negative_decision = any(term in source for term in ("没有通过", "未通过", "不通过"))
    decisions: list[str] = []
    for term in _DECISION_FACT_TERMS:
        if term not in source:
            continue
        if term == "通过" and negative_decision:
            continue
        decisions.append(term)
    return tuple(numbers), tuple(owners), tuple(entities), tuple([*dates, *decisions])


def _semantic_equivalence_text(text: str) -> str:
    normalized = normalize_asr_terms(str(text or ""))

    def replace_number(match: re.Match[str]) -> str:
        groups = match.groupdict()
        if groups.get("percent_cn"):
            return f" MC_PERCENT_{_canonical_number(groups['percent_cn'])} "
        if groups.get("percent_ar"):
            return f" MC_PERCENT_{_canonical_number(groups['percent_ar'])} "
        if groups.get("percent_pai"):
            return f" MC_PERCENT_{_canonical_number(groups['percent_pai'])} "
        if groups.get("weekday"):
            weekday = "7" if groups["weekday"] in {"日", "天"} else str(_CHINESE_DIGITS[groups["weekday"]])
            return f" MC_WEEKDAY_{weekday} "
        if groups.get("cn_unit"):
            return f" MC_{groups['cn_unit_name']}_{_canonical_number(groups['cn_unit'])} "
        if groups.get("ar_unit"):
            return f" MC_{groups['ar_unit_name']}_{_canonical_number(groups['ar_unit'])} "
        return match.group(0)

    return _similarity_text(_NUMBER_FACT_RE.sub(replace_number, normalized))


def _canonical_number(value: str) -> str:
    raw = str(value or "").strip()
    if re.fullmatch(r"\d+(?:\.\d+)?", raw):
        return _trim_decimal(raw)
    if "点" in raw:
        integer_part, decimal_part = raw.split("点", 1)
        integer = _chinese_integer(integer_part)
        decimals = "".join(str(_CHINESE_DIGITS[char]) for char in decimal_part if char in _CHINESE_DIGITS)
        return _trim_decimal(f"{integer}.{decimals or '0'}")
    return str(_chinese_integer(raw))


def _chinese_integer(value: str) -> int:
    if value and all(char in _CHINESE_DIGITS for char in value):
        return int("".join(str(_CHINESE_DIGITS[char]) for char in value))
    total = 0
    section = 0
    number = 0
    for char in value:
        if char in _CHINESE_DIGITS:
            number = _CHINESE_DIGITS[char]
            continue
        unit = _CHINESE_UNITS.get(char)
        if unit is None:
            continue
        if unit >= 10_000:
            section = (section + number) * unit
            total += section
            section = 0
        else:
            section += (number or 1) * unit
        number = 0
    return total + section + number


def _trim_decimal(value: str) -> str:
    if "." not in value:
        return str(int(value))
    return value.rstrip("0").rstrip(".")


def _similarity_text(text: str) -> str:
    return "".join(character.lower() for character in text if character.isalnum() or "\u4e00" <= character <= "\u9fff")


def _original_evidence(
    payload: dict[str, Any],
    *,
    segment_id: str,
    text: str,
    start_ms: int,
    end_ms: int,
) -> dict[str, Any]:
    evidence_spans = list(payload.get("evidence_spans") or [])
    if evidence_spans:
        return dict(evidence_spans[0])
    return {
        "id": f"asr_ev_{segment_id}",
        "segment_id": segment_id,
        "quote": text,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "status": "active",
    }


def _safe_usage(usage: dict[str, Any]) -> dict[str, int]:
    return {
        key: max(0, int(usage.get(key) or 0))
        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
    }


def _safe_audit_value(value: Any, *, fallback: str) -> str:
    normalized = str(value or "").strip()
    lowered = normalized.lower()
    if (
        not _AUDIT_VALUE_RE.fullmatch(normalized)
        or "://" in normalized
        or "api_key" in lowered
        or "authorization" in lowered
        or re.search(r"(?:^|[^a-z0-9])sk-[a-z0-9_-]+", lowered)
    ):
        return fallback
    return normalized


def _safe_batch_id(batch_id: str) -> str:
    normalized = str(batch_id or "").strip()
    if not _BATCH_ID_RE.fullmatch(normalized):
        raise ValueError("unsafe realtime correction batch_id")
    return normalized
