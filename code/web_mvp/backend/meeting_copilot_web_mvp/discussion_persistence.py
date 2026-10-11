"""Bounded, source-bound working state for this meeting, never project facts."""
from __future__ import annotations

import hashlib
import json
import time
from types import SimpleNamespace
from typing import Any, Mapping, Sequence


PARTICIPATION_INTENTS = {
    "ask_question": "结合当前讨论，帮我提出一个尚未被回应、值得当场澄清的问题。",
    "compare_options": "结合当前讨论，帮我提出一个有建设性的备选方案或取舍，并说明适用条件。",
    "formulate_response": "结合当前讨论，帮我组织一句有依据、可以当场说出口的回应。",
    "freeform": "结合当前讨论，帮我提出一句有增量、可以当场说出口的建设性观点。",
}
DISCUSSION_ACTIVE_STATES = {"investigating", "ready", "waiting_for_evidence", "proposed"}
_CONTEXT_FIELDS = {
    "current_problem", "current_solution", "alternatives", "constraints", "responded_points",
    "open_points", "inferred", "phase", "analysis_basis_revision", "last_checked_seq",
    "opportunity_formed_at_ms",
    "proposals",
    "last_semantic_seq",
    "reanalysis",
    "current_attention",
}
_REANALYSIS_DEFER_REASONS = {"evidence_changing", "priority_request", "background_busy"}
_ATTENTION_CONTRACT = "discussion_attention.v1"
CURRENT_NEED_CONTRACT = "participation_need.v1"


def validate_participation_current_need(value: Any, *, metadata: Mapping[str, Any],
                                        paragraphs_by_id: Mapping[str, Any], action: str) -> dict[str, Any] | None:
    """Bind the same Pi call's need assessment to its frozen latest sources.

    Source validity does not prove the model's semantic judgement. This checks
    its declared judgement and references, without guessing from new words or
    advancing the state-understanding cursor.
    """
    if "current_need_contract" not in metadata:
        return None
    direct = metadata.get("automatic_direct") is True
    if (metadata.get("current_need_contract") != CURRENT_NEED_CONTRACT
            or not (direct or metadata.get("automatic_attention") is True) or metadata.get("profile") != "participation"):
        raise ValueError("current need contract is invalid")
    basis = metadata.get("current_need_basis")
    if not isinstance(basis, Mapping) or set(basis) != {
            "attention_covered_seq", "packet_anchor_seq", "focus_segment_ids", "unassessed_segment_ids"}:
        raise ValueError("current need frozen basis is invalid")
    covered, anchor = basis["attention_covered_seq"], basis["packet_anchor_seq"]
    attention = metadata.get("current_attention") or {}
    if not isinstance(attention, Mapping):
        raise ValueError("current need frozen attention is invalid")
    if (type(covered) is not int or type(anchor) is not int or not 0 <= covered <= anchor
            or anchor != metadata.get("anchor_seq")
            or (covered != 0 if direct else covered != attention.get("covered_seq")
                or not _attention_ready({"current_attention": attention}))):
        raise ValueError("current need coverage is invalid")
    frozen_ids = {item["segment_id"] for item in metadata.get("evidence") or []}
    focus, unassessed = basis["focus_segment_ids"], basis["unassessed_segment_ids"]
    if (not isinstance(focus, list) or not 1 <= len(focus) <= 2
            or any(not isinstance(item, str) or item not in frozen_ids for item in focus)
            or len(set(focus)) != len(focus) or not isinstance(unassessed, list) or len(unassessed) > 2
            or any(not isinstance(item, str) or item not in focus for item in unassessed)
            or len(set(unassessed)) != len(unassessed) or (anchor > covered and not unassessed)):
        raise ValueError("current need focus is invalid")
    if not isinstance(value, Mapping) or set(value) != {"status", "focus_evidence", "reason"}:
        raise ValueError("current need assessment is required")
    status, reason, evidence = value["status"], value["reason"], value["focus_evidence"]
    if (not isinstance(status, str) or status not in {"has_current_need", "already_answered", "moved_on", "uncertain"}
            or not isinstance(reason, str) or not reason.strip() or len(reason) > 160
            or not isinstance(evidence, list) or not 1 <= len(evidence) <= 2):
        raise ValueError("current need assessment is invalid")
    if action not in {"intervention", "silent"} or (action == "intervention" and status != "has_current_need"):
        raise ValueError("current need does not permit this intervention")
    normalized = []
    for entry in evidence:
        if not isinstance(entry, Mapping) or set(entry) != {"segment_id", "quote"}:
            raise ValueError("current need evidence is invalid")
        source_id, quote = entry["segment_id"], entry["quote"]
        source = paragraphs_by_id.get(source_id) if isinstance(source_id, str) else None
        text = source if isinstance(source, str) else getattr(source, "text", None)
        if (source_id not in focus or not isinstance(quote, str) or not quote.strip() or len(quote) > 300
                or not isinstance(text, str) or quote not in text):
            raise ValueError("current need evidence does not quote the frozen focus")
        normalized.append({"segment_id": source_id, "quote": quote})
    if len({(item["segment_id"], item["quote"]) for item in normalized}) != len(normalized):
        raise ValueError("current need evidence is duplicated")
    if unassessed and not any(item["segment_id"] in unassessed for item in normalized):
        raise ValueError("current need has not assessed the latest frozen exchange")
    return {"status": status, "focus_evidence": normalized, "reason": reason.strip()}


def _invalidate_current_attention(context: dict[str, Any]) -> None:
    """Revoke current opportunity, retaining historical discussion ownership."""
    previous = context.get("current_attention")
    if not isinstance(previous, Mapping):
        return
    context["current_attention"] = {**previous,
        "current_focus": {"observations": [], "relation": "unknown", "object": None, "setting": None, "purpose": None},
        "contribution_opportunity": {"status": "uncertain", "intent": None,
            "reason": "依据已更新，等待重新理解。", "evidence_segment_ids": [], "evidence_quote": "", "opportunity_id": None},
        "opportunity_id": None, "source_bindings": [],
        "control_revision": int(previous.get("control_revision") or 0) + 1}


def _attention_ready(context: Mapping[str, Any]) -> bool:
    value = context.get("current_attention")
    return bool(isinstance(value, Mapping) and value.get("contract") == _ATTENTION_CONTRACT
        and isinstance(value.get("opportunity_id"), str) and value["opportunity_id"]
        and type(value.get("control_revision")) is int and value["control_revision"] > 0
        and (value.get("contribution_opportunity") or {}).get("status") == "present"
        and value.get("source_bindings"))


def _reanalysis_clock(context: Mapping[str, Any]) -> dict[str, Any] | None:
    value = context.get("reanalysis")
    if not isinstance(value, Mapping) or set(value) - {"first_revision_at_ms", "last_revision_at_ms", "defer_reason"}:
        return None
    first, last = value.get("first_revision_at_ms"), value.get("last_revision_at_ms")
    if type(first) is not int or type(last) is not int or not 0 <= first <= last:
        return None
    if "defer_reason" in value and (not isinstance(value["defer_reason"], str) or value["defer_reason"] not in _REANALYSIS_DEFER_REASONS):
        return None
    return dict(value)


def participation_request(intent: str | None, request: str) -> tuple[str | None, str]:
    normalized = str(intent or "").strip() or None
    text = " ".join(str(request or "").split())
    if normalized is not None:
        if normalized not in PARTICIPATION_INTENTS:
            raise ValueError("unsupported participation intent")
        text = text or PARTICIPATION_INTENTS[normalized]
    if not text or len(text) > 4_000:
        raise ValueError("user_request must contain 1 to 4000 characters")
    return normalized, text


def bounded_discussion_context(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) - _CONTEXT_FIELDS:
        raise ValueError("unsupported discussion context fields")
    result = dict(value)
    for key, limit in (("alternatives", 3), ("proposals", 3), ("constraints", 8), ("open_points", 8), ("responded_points", 8)):
        if key in result and (not isinstance(result[key], list) or len(result[key]) > limit):
            raise ValueError(f"discussion {key} must be a bounded list")
    for key in ("current_problem", "current_solution", "phase"):
        if key in result and (not isinstance(result[key], str) or len(result[key]) > 1_000):
            raise ValueError(f"discussion {key} must be bounded text")
    for key in ("analysis_basis_revision", "last_checked_seq", "last_semantic_seq"):
        if key in result and (type(result[key]) is not int or result[key] < (1 if key == "analysis_basis_revision" else 0)):
            raise ValueError(f"invalid discussion {key}")
    if "reanalysis" in result and _reanalysis_clock(result) is None:
        raise ValueError("invalid discussion reanalysis clock")
    if "current_attention" in result:
        attention = result["current_attention"]
        fields = {"current_focus", "contribution_opportunity", "contract", "control_revision", "opportunity_id", "covered_seq", "source_bindings"}
        if (not isinstance(attention, Mapping) or set(attention) != fields
                or attention.get("contract") != _ATTENTION_CONTRACT
                or type(attention.get("control_revision")) is not int or attention["control_revision"] < 1
                or type(attention.get("covered_seq")) is not int or attention["covered_seq"] < 0
                or not isinstance(attention.get("source_bindings"), list) or len(attention["source_bindings"]) > 12):
            raise ValueError("invalid host discussion attention")
    encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
    if len(encoded) > 6_000:
        raise ValueError("discussion context must not exceed 6000 characters")
    return json.loads(encoded)


def invalidate_discussion_inputs(connection, meeting_id: str, now_ms: int) -> None:
    """Called on the source writer transaction; preserve history, mark recheck."""
    from .user_memory import input_context_state
    from .knowledge_invalidation import _append

    if not connection.in_transaction:
        raise RuntimeError("discussion source invalidation requires a write transaction")
    for row in connection.execute("SELECT * FROM agent_work_items WHERE meeting_id=? AND kind='discussion' AND state IN ('proposed','investigating','waiting_for_evidence','ready')", (meeting_id,)).fetchall():
        binding = json.loads(row["input_context_json"] or "{}")
        context = json.loads(row["context_json"] or "{}")
        if not binding or input_context_state(connection, meeting_id, binding) == "valid" or context.get("phase") == "needs_review":
            continue
        context.update(analysis_basis_revision=int(context.get("analysis_basis_revision") or 1) + 1, phase="needs_review", inferred=True)
        _invalidate_current_attention(context)
        connection.execute("UPDATE agent_work_items SET context_json=?,state='waiting_for_evidence',version=version+1,updated_at_ms=? WHERE work_item_id=?", (json.dumps(context, ensure_ascii=False), now_ms, row["work_item_id"]))
        item = dict(row)
        item.update(context=context, input_context=binding, state="waiting_for_evidence", version=int(row["version"]) + 1, updated_at_ms=now_ms)
        for field in ("context_json", "input_context_json", "evidence_json", "evidence_segment_ids_json", "needed_json"):
            item.pop(field, None)
        _append(connection, meeting_id, "meeting.discussion.updated", row["work_item_id"], now_ms,
                f"discussion.updated:{row['work_item_id']}:{item['version']}", {"discussion": item})


class DiscussionPersistence:
    """Mixin whose writes always share V2Persistence's existing transaction."""

    def _current_discussion_locked(self, meeting_id: str):
        return self._conn.execute(
            "SELECT * FROM agent_work_items WHERE meeting_id=? AND kind='discussion' "
            "AND state IN ('proposed','investigating','waiting_for_evidence','ready') "
            "ORDER BY created_at_ms DESC,work_item_id DESC LIMIT 1", (meeting_id,),
        ).fetchone()

    def _preempt_automatic_analysis_locked(self, meeting_id: str, now_ms: int) -> list[str]:
        rows = self._conn.execute(
            "SELECT id FROM jobs WHERE meeting_id=? AND kind='intelligence' "
            "AND trigger_type IN ('discussion_update','task_due','answer_ready') "
            "AND status IN ('pending','retry_wait','running')", (meeting_id,),
        ).fetchall()
        ids = [row["id"] for row in rows]
        for job_id in ids:
            self._conn.execute("UPDATE jobs SET status='cancelled',lease_owner=NULL,lease_until_ms=NULL,error_class='preempted_by_user_request',completed_at_ms=?,updated_at_ms=? WHERE id=?", (now_ms, now_ms, job_id))
        return ids

    def _discussion_event_locked(self, row, now_ms: int) -> dict[str, Any]:
        item = self._agent_work_item_dict(row)
        self._append_event_locked(
            meeting_id=row["meeting_id"], event_type="meeting.discussion.updated", aggregate_type="agent_work_item",
            aggregate_id=row["work_item_id"], occurred_at_ms=now_ms,
            idempotency_key=f"discussion.updated:{row['work_item_id']}:{row['version']}",
            payload={"discussion": item}, correlation_id=row["meeting_id"],
        )
        return item

    def _ensure_discussion_locked(self, meeting_id: str, *, now_ms: int, topic_id: str | None = None):
        from .user_memory import capture_input_context, input_context_state

        current = self._current_discussion_locked(meeting_id)
        if topic_id and (current is None or current["work_item_id"] != topic_id):
            raise RuntimeError("discussion topic conflict")
        if current is not None:
            binding = json.loads(current["input_context_json"] or "{}")
            if binding and input_context_state(self._conn, meeting_id, binding) != "valid":
                context = json.loads(current["context_json"] or "{}")
                if context.get("phase") != "needs_review":
                    context["analysis_basis_revision"] = int(context.get("analysis_basis_revision") or 1) + 1
                # An explicit zero cursor preserves canonical-revision recheck
                # until semantic success, including after deferred admission.
                if context.get("phase") != "needs_review" or context.get("last_semantic_seq") != 0:
                    context["phase"] = "discussing"
                _invalidate_current_attention(context)
                self._cancel_discussion_jobs_locked(meeting_id, current["work_item_id"], now_ms, "discussion_sources_changed")
                self._conn.execute(
                    "UPDATE agent_work_items SET context_json=?,input_context_json=?,version=version+1,updated_at_ms=? WHERE work_item_id=?",
                    (json.dumps(context, ensure_ascii=False), json.dumps(capture_input_context(self._conn, meeting_id, "general"), ensure_ascii=False), now_ms, current["work_item_id"]),
                )
                current = self._conn.execute("SELECT * FROM agent_work_items WHERE work_item_id=?", (current["work_item_id"],)).fetchone()
                self._discussion_event_locked(current, now_ms)
            return current
        latest = self._conn.execute(
            "SELECT * FROM transcript_segments WHERE meeting_id=? AND duplicate_of_segment_id IS NULL ORDER BY transcript_seq DESC LIMIT 1", (meeting_id,),
        ).fetchone()
        if latest is None:
            raise ValueError("当前会议还没有可供教练分析的文字")
        count = self._conn.execute("SELECT COUNT(*) FROM agent_work_items WHERE meeting_id=? AND kind='discussion'", (meeting_id,)).fetchone()[0]
        identity = hashlib.sha256(f"{meeting_id}:{latest['segment_id']}:{count}".encode()).hexdigest()[:24]
        work_item_id = f"discussion:{identity}"
        text = str(latest["normalized_text"] or latest["text"] or "")
        context = bounded_discussion_context({"current_problem": text[:800], "current_solution": "", "alternatives": [], "constraints": [],
                                             "responded_points": [], "open_points": [], "inferred": True, "phase": "discussing",
                                             "analysis_basis_revision": 1, "last_checked_seq": int(latest["transcript_seq"])})
        self._conn.execute(
            "INSERT INTO agent_work_items(work_item_id,meeting_id,dedupe_key,kind,state,priority,title,summary,trigger_type,"
            "last_execution_status,version,created_at_ms,updated_at_ms,context_json,input_context_json) "
            "VALUES(?,?,?,'discussion','investigating',120,?,?,'user_request','not_started',1,?,?,?,?)",
            (work_item_id, meeting_id, work_item_id, text[:80] or "当前讨论", text[:800], now_ms, now_ms,
             json.dumps(context, ensure_ascii=False), json.dumps(capture_input_context(self._conn, meeting_id, "general"), ensure_ascii=False)),
        )
        current = self._conn.execute("SELECT * FROM agent_work_items WHERE work_item_id=?", (work_item_id,)).fetchone()
        self._discussion_event_locked(current, now_ms)
        return current

    def get_current_discussion(self, meeting_id: str) -> dict[str, Any] | None:
        with self._lock:
            self._raise_if_tombstoned_locked(meeting_id)
            row = self._current_discussion_locked(meeting_id)
            return self._agent_work_item_dict(row) if row is not None else None

    def get_discussion_state_capability(self, meeting_id: str, *, now_ms: int | None = None) -> dict[str, Any]:
        """Project the current understanding lane independently of coach cards."""
        from .user_memory import input_context_state

        now_ms = time.time_ns() // 1_000_000 if now_ms is None else now_ms
        with self._lock:
            self._raise_if_tombstoned_locked(meeting_id)
            current = self._current_discussion_locked(meeting_id)
            context = json.loads(current["context_json"] or "{}") if current is not None else {}
            topic_id = current["work_item_id"] if current is not None else None
            basis = int(context.get("analysis_basis_revision") or 1) if current is not None else None
            last_semantic_seq = int(context.get("last_semantic_seq") or 0)
            latest_seq = int(self._conn.execute(
                "SELECT COALESCE(MAX(transcript_seq),0) FROM transcript_segments "
                "WHERE meeting_id=? AND duplicate_of_segment_id IS NULL", (meeting_id,)).fetchone()[0])
            result = {"state": "idle", "label": "等待足够讨论内容", "detail": "原文会持续保留；可随时主动提问。",
                "decision": "not_started", "error_class": None, "job_id": None, "topic_id": topic_id,
                "analysis_basis_revision": basis, "input_transcript_seq": latest_seq, "last_semantic_seq": last_semantic_seq}
            if current is None:
                return result
            current_binding = json.loads(current["input_context_json"] or "{}")
            if current_binding and input_context_state(self._conn, meeting_id, current_binding) != "valid":
                return {**result, "understanding_status": "waiting_reanalysis", "label": "资料已更新，等待重新理解"}
            # Read the lane directly. The UI's bounded snapshot jobs page is not
            # an authoritative way to infer that background work is absent.
            rows = self._conn.execute("SELECT * FROM jobs WHERE meeting_id=? AND kind='intelligence' "
                "AND json_extract(request_context_json,'$.task_mode')='discussion_state' "
                "AND ((json_extract(request_context_json,'$.topic_id')=? AND json_extract(request_context_json,'$.analysis_basis_revision')=?) "
                "OR (status='succeeded' AND json_extract(output_json,'$.discussion_identity.topic_id')=? "
                "AND json_extract(output_json,'$.discussion_identity.analysis_basis_revision')=?)) "
                "ORDER BY created_at_ms DESC,id DESC", (meeting_id, topic_id, basis, topic_id, basis)).fetchall()
            for row in rows:
                metadata = json.loads(row["request_context_json"] or "{}")
                output = json.loads(row["output_json"] or "{}")
                identity = output.get("discussion_identity") or {} if row["status"] == "succeeded" else metadata
                if identity.get("topic_id") != topic_id or identity.get("analysis_basis_revision") != basis:
                    continue
                check_metadata = {**metadata, "task_mode": None, "topic_id": topic_id, "analysis_basis_revision": basis}
                if self._discussion_request_state_locked(meeting_id, check_metadata) != "valid":
                    continue
                status = row["status"]
                if status not in {"pending", "running", "retry_wait", "succeeded", "failed", "cancelled"}:
                    continue
                if status not in {"pending", "running", "retry_wait"} and int(row["input_transcript_seq"]) != latest_seq:
                    continue
                common = {**result, "decision": status, "job_id": row["id"], "input_transcript_seq": int(row["input_transcript_seq"])}
                if status in {"pending", "retry_wait"}:
                    if context.get("phase") == "needs_review":
                        return {**common, "understanding_status": "reanalysis_queued", "label": "校对后重新理解已排队",
                            "detail": "最新版本已保存，等待后台执行；旧依据不会作为本轮结果。"}
                    return {**common, "label": "本轮理解等待执行", "detail": "正在等待后台预算；原文已保留，可继续讨论或主动提问。"}
                if status == "running":
                    if context.get("phase") == "needs_review":
                        return {**common, "state": "busy", "understanding_status": "reanalysis_running",
                            "label": "正在理解校对后的讨论", "detail": "正在核对最新版本，原文及历史建议继续保留。"}
                    return {**common, "state": "busy", "label": "正在理解本轮讨论", "detail": "本轮尚未形成可靠讨论状态，原文已保留。"}
                if status == "succeeded":
                    if context.get("phase") == "discussing" and last_semantic_seq >= int(row["input_transcript_seq"]):
                        applied = output.get("applied")
                        projection = output.get("state_projection") or (applied.get("state_projection") if isinstance(applied, Mapping) else None) or {}
                        rejected_count = projection.get("rejected_operation_count") if isinstance(projection, Mapping) else None
                        if type(rejected_count) is int and 0 < rejected_count <= 10_000:
                            return {**common, "state": "active", "label": "部分问题闭环待核对",
                                "understanding_status": "partial_review",
                                "detail": f"已保存有效话题和事实；{rejected_count}条问题闭环证据不足，未标为已回应。",
                                "projection_diagnostics": {"rejected_operation_count": rejected_count}}
                        return {**common, "state": "active", "label": "本轮讨论已理解", "detail": "当前方案和未决点已更新，等待值得提出的建议。"}
                    continue
                if status == "cancelled":
                    return {**common, "label": "本轮理解已取消", "detail": "原文已保留；输入更新或主动操作可取消后台理解。"}
                error = str(row["error_class"] or "")
                reason = ("evidence_rejected" if error == "intelligence_validation_evidence" else
                    "invalid_output" if error.startswith("intelligence_validation_") else
                    "provider_timeout" if error in {"deadline_exceeded", "IntelligenceDeadlineExceeded", "TimeoutError", "ReadTimeout"} else
                    "provider_unavailable" if error in {"ProviderRuntimeNotConfiguredDeferred", "ConnectError", "ConnectTimeout"} else "unknown_failure")
                return {**common, "state": "paused", "label": "本轮理解未完成", "error_class": reason,
                    "detail": "本轮理解未通过依据核对，原文已保留；继续讨论或主动提问仍可用。" if reason == "evidence_rejected" else
                    "本轮理解暂未形成可靠状态，原文已保留；继续讨论或主动提问仍可用。"}
            if context.get("phase") == "discussing" and last_semantic_seq >= latest_seq and last_semantic_seq > 0:
                # Older successful outputs predate the explicit identity field;
                # trust the durable semantic cursor, without inventing job identity.
                return {**result, "state": "active", "decision": "succeeded", "label": "本轮讨论已理解",
                    "detail": "当前方案和未决点已更新，等待值得提出的建议。"}
            if context.get("phase") == "needs_review":
                clock = _reanalysis_clock(context)
                reason = clock.get("defer_reason") if clock else None
                if reason == "evidence_changing" and now_ms - clock["last_revision_at_ms"] < 2_000 and now_ms - clock["first_revision_at_ms"] >= 15_000:
                    return {**result, "understanding_status": "revision_deferred", "error_class": "evidence_changing",
                        "label": "校对仍在更新，重新理解暂缓", "detail": "已保留最新版本；等待文字稳定后合并一次理解。"}
                if reason in {"priority_request", "background_busy"}:
                    trigger_filter = "trigger_type='user_request'" if reason == "priority_request" else "json_extract(request_context_json,'$.task_mode')='discussion_state'"
                    blocker = self._conn.execute("SELECT 1 FROM jobs WHERE meeting_id=? AND kind='intelligence' "
                        "AND status IN ('pending','retry_wait','running') AND " + trigger_filter + " LIMIT 1", (meeting_id,)).fetchone()
                    if blocker:
                        return {**result, "understanding_status": "reanalysis_deferred", "error_class": reason,
                            "label": "校对后重新理解暂缓", "detail": "优先请求或先前任务仍在处理；最新版本不会丢失。"}
                return {**result, "understanding_status": "waiting_reanalysis", "label": "校对后等待重新理解",
                    "detail": "旧理解已失效；最新文字稳定后将合并分析，可继续讨论或主动提问。"}
            return {**result, "label": "有新内容，等待下一轮理解" if latest_seq else result["label"]}

    def get_material_context(self, meeting_id: str, *, provided_evidence=()) -> dict[str, Any]:
        from .meeting_materials import material_context_snapshot
        with self._lock:
            self._raise_if_tombstoned_locked(meeting_id)
            return material_context_snapshot(self._conn, meeting_id, provided_evidence=provided_evidence)

    def discussion_request_state(self, meeting_id: str, metadata: Mapping[str, Any], *, require_latest: bool = False) -> str:
        with self._lock:
            return self._discussion_request_state_locked(meeting_id, metadata, require_latest=require_latest)

    def _discussion_request_state_locked(self, meeting_id: str, metadata: Mapping[str, Any], *, require_latest: bool = False) -> str:
        from .user_memory import input_context_state

        if metadata.get("profile") != "participation":
            return "valid"
        if metadata.get("task_mode") == "discussion_state" or metadata.get("automatic_direct") is True:
            meeting = self._conn.execute("SELECT state FROM meetings WHERE id=?", (meeting_id,)).fetchone()
            if meeting is None or meeting["state"] != "live":
                return "meeting_ended"
        if require_latest and metadata.get("automatic_direct") is True:
            latest_seq = self._conn.execute("SELECT COALESCE(MAX(transcript_seq),0) FROM transcript_segments "
                "WHERE meeting_id=? AND duplicate_of_segment_id IS NULL", (meeting_id,)).fetchone()[0]
            if latest_seq != metadata.get("anchor_seq"):
                return "focus_superseded"
        current = self._current_discussion_locked(meeting_id)
        if current is None or current["work_item_id"] != metadata.get("topic_id"):
            return "topic_superseded"
        context = json.loads(current["context_json"] or "{}")
        if int(context.get("analysis_basis_revision") or 1) != metadata.get("analysis_basis_revision"):
            return "basis_superseded"
        binding = metadata.get("input_context") or {}
        if binding and input_context_state(self._conn, meeting_id, binding) != "valid":
            return "sources_superseded"
        for evidence in metadata.get("evidence") or []:
            row = self._conn.execute("SELECT revision,evidence_hash FROM transcript_segments WHERE meeting_id=? AND segment_id=?", (meeting_id, evidence.get("segment_id"))).fetchone()
            if row is None or int(row["revision"]) != evidence.get("revision") or row["evidence_hash"] != evidence.get("evidence_hash"):
                return "evidence_superseded"
        if metadata.get("automatic_attention") is True:
            current_attention = context.get("current_attention") or {}
            frozen = metadata.get("current_attention") or {}
            if (not _attention_ready(context) or frozen.get("opportunity_id") != current_attention.get("opportunity_id")
                    or frozen.get("control_revision") != current_attention.get("control_revision")):
                return "attention_superseded"
        return "valid"

    def _cancel_discussion_jobs_locked(self, meeting_id: str, topic_id: str, now_ms: int, reason: str, *, excluding_job_id: str | None = None,
                                       retained_decision_ids: frozenset[str] = frozenset()) -> list[str]:
        cancelled = []
        for job in self._conn.execute("SELECT id,request_context_json FROM jobs WHERE meeting_id=? AND kind='intelligence' AND status IN ('pending','retry_wait','running')", (meeting_id,)).fetchall():
            metadata = json.loads(job["request_context_json"] or "{}")
            if job["id"] != excluding_job_id and metadata.get("profile") == "participation" and metadata.get("topic_id") == topic_id:
                self._conn.execute("UPDATE jobs SET status='cancelled',lease_owner=NULL,lease_until_ms=NULL,error_class=?,completed_at_ms=?,updated_at_ms=? WHERE id=?", (reason, now_ms, now_ms, job["id"]))
                cancelled.append(job["id"])
        for binding in self._conn.execute("SELECT * FROM coach_source_bindings WHERE meeting_id=? AND invalidated_at_ms IS NULL", (meeting_id,)).fetchall():
            decision = json.loads(binding["payload_json"] or "{}")
            if decision.get("topic_id") != topic_id or binding["decision_id"] in retained_decision_ids:
                continue
            self._conn.execute("UPDATE coach_source_bindings SET invalidated_at_ms=? WHERE meeting_id=? AND decision_id=?", (now_ms, meeting_id, binding["decision_id"]))
            decision.update(decision_id=f"{binding['decision_id']}:discussion-stale", status="stale", status_reason=reason,
                            decision_reason="议题或依据已变化，这条建议已收束。", supersedes_decision_id=binding["decision_id"],
                            lifecycle_refresh=True, lifecycle_action="retract", lifecycle_refresh_previous_decision_id=binding["decision_id"])
            self._append_event_locked(meeting_id=meeting_id, event_type="meeting.intelligence.applied", aggregate_type="intelligence",
                                      aggregate_id=binding["job_id"], occurred_at_ms=now_ms,
                                      idempotency_key=f"discussion.retract:{binding['decision_id']}",
                                      payload={"job_id": binding["job_id"], "coach_decision": decision, "coach_intervention": None, "follow_up": None},
                                      correlation_id=meeting_id, causation_id="discussion_context")
        return cancelled

    def _continuing_coach_card_locked(self, *, meeting_id: str, topic_id: str, context: Mapping[str, Any],
                                      attention, metadata: Mapping[str, Any] | None, now_ms: int) -> frozenset[str]:
        """Keep a proven published contribution, without authorizing old work.

        Its original job, quotes, generation basis and expiry remain immutable.
        Every failure to establish continuity follows the existing withdrawal.
        """
        from .knowledge_context import source_evidence_state
        from .realtime_intelligence import IntelligenceResponseValidationError, _validate_evidence_quote
        from .user_memory import input_context_state

        if not metadata or not _attention_ready(context):
            return frozenset()
        value = self._validated_attention_locked(meeting_id, attention, metadata)
        if value is None or value["current_focus"]["relation"] != "same_topic":
            return frozenset()
        previous = context["current_attention"]
        opportunity = value["contribution_opportunity"]
        if (opportunity["status"] != "present" or opportunity["opportunity_id"] != previous["opportunity_id"]
                or opportunity["intent"] != previous["contribution_opportunity"]["intent"]):
            return frozenset()
        active = self._latest_active_coach_intervention_decision_locked(meeting_id, excluding_decision_id="")
        if active is None or active.get("topic_id") != topic_id:
            return frozenset()
        binding = self._conn.execute("SELECT * FROM coach_source_bindings WHERE meeting_id=? AND decision_id=? "
            "AND invalidated_at_ms IS NULL", (meeting_id, active["decision_id"])).fetchone()
        if binding is None:
            return frozenset()
        job = self._conn.execute("SELECT * FROM jobs WHERE id=? AND meeting_id=? AND kind='intelligence' "
            "AND status='succeeded' AND trigger_type='discussion_update'", (binding["job_id"], meeting_id)).fetchone()
        if job is None:
            return frozenset()
        frozen = json.loads(job["request_context_json"] or "{}")
        original_attention = frozen.get("current_attention") or {}
        if (frozen.get("current_need_contract") != CURRENT_NEED_CONTRACT or frozen.get("automatic_attention") is not True
                or frozen.get("profile") != "participation" or frozen.get("task_mode") == "discussion_state"
                or frozen.get("topic_id") != topic_id or original_attention.get("opportunity_id") != previous["opportunity_id"]
                or (original_attention.get("contribution_opportunity") or {}).get("intent") != opportunity["intent"]):
            return frozenset()
        event = self._conn.execute("SELECT payload_json FROM meeting_events WHERE meeting_id=? AND idempotency_key=?",
            (meeting_id, f"meeting.intelligence.applied:{job['id']}")).fetchone()
        payload = json.loads(event["payload_json"] or "{}") if event else {}
        card, decision = payload.get("coach_intervention"), payload.get("coach_decision") or {}
        if (not isinstance(card, Mapping) or decision.get("decision_id") != binding["decision_id"]
                or decision.get("status") != "intervention" or decision.get("lifecycle_action") != "retain"):
            return frozenset()
        for original in (card, decision):
            expiry = original.get("valid_until_ms")
            if expiry is not None and (type(expiry) is not int or expiry <= now_ms):
                return frozenset()
        paragraphs = {}
        for item in frozen.get("evidence") or []:
            source = self._conn.execute("SELECT * FROM transcript_segments WHERE meeting_id=? AND segment_id=? "
                "AND duplicate_of_segment_id IS NULL", (meeting_id, item.get("segment_id"))).fetchone()
            if source is None or (source["revision"], source["evidence_hash"]) != (item.get("revision"), item.get("evidence_hash")):
                return frozenset()
            paragraphs[source["segment_id"]] = SimpleNamespace(text=str(source["normalized_text"] or source["text"] or ""))
        try:
            proof = validate_participation_current_need(payload.get("current_need"), metadata=frozen,
                paragraphs_by_id=paragraphs, action="intervention")
        except ValueError:
            return frozenset()
        if (proof is None or not card.get("evidence_segment_ids")
                or not set(card["evidence_segment_ids"]).issubset(paragraphs) or not isinstance(card.get("evidence_quote"), str)):
            return frozenset()
        try:
            # Use the publication contract: each exact nonempty quote line may
            # come from a different declared, still-current frozen paragraph.
            _validate_evidence_quote(card["evidence_quote"], evidence_ids=tuple(card["evidence_segment_ids"]),
                request=SimpleNamespace(paragraphs_by_id=paragraphs), field="intervention.evidence_quote")
        except IntelligenceResponseValidationError:
            return frozenset()
        for sources in (frozen.get("source_evidence") or [], json.loads(binding["source_evidence_json"] or "[]")):
            if sources and source_evidence_state(self._conn, meeting_id, sources) != "valid":
                return frozenset()
        for inputs in (frozen.get("input_context") or {}, json.loads(binding["input_context_json"] or "{}")):
            if not inputs or input_context_state(self._conn, meeting_id, inputs) != "valid":
                return frozenset()
        return frozenset({binding["decision_id"]})

    def update_discussion_context(self, *, meeting_id: str, work_item_id: str, expected_version: int,
                                  now_ms: int, context_patch: Mapping[str, Any] | None = None,
                                  state: str | None = None, title: str | None = None) -> dict[str, Any]:
        if type(expected_version) is not int or expected_version < 1:
            raise ValueError("discussion expected_version is required")
        if state is not None and state not in DISCUSSION_ACTIVE_STATES | {"resolved", "dismissed", "superseded"}:
            raise ValueError("unsupported discussion state")
        patch = dict(context_patch or {})
        if {"analysis_basis_revision", "last_checked_seq", "last_semantic_seq", "proposals", "opportunity_formed_at_ms", "reanalysis", "current_attention"} & set(patch):
            raise ValueError("discussion revisions are host managed")
        if title is not None and (not str(title).strip() or len(str(title)) > 200):
            raise ValueError("discussion title must contain 1 to 200 characters")
        with self._write_transaction():
            self._raise_if_tombstoned_locked(meeting_id)
            row = self._conn.execute("SELECT * FROM agent_work_items WHERE meeting_id=? AND work_item_id=? AND kind='discussion'", (meeting_id, work_item_id)).fetchone()
            if row is None:
                raise KeyError("discussion not found")
            if int(row["version"]) != expected_version:
                raise RuntimeError("discussion version conflict")
            context = json.loads(row["context_json"] or "{}")
            context.update(patch)
            context["analysis_basis_revision"] = int(context.get("analysis_basis_revision") or 1) + 1
            _invalidate_current_attention(context)
            context = bounded_discussion_context(context)
            self._cancel_discussion_jobs_locked(meeting_id, work_item_id, now_ms, "discussion_basis_changed")
            self._conn.execute("UPDATE agent_work_items SET context_json=?,state=?,title=?,version=version+1,updated_at_ms=?,resolved_at_ms=? WHERE work_item_id=?",
                               (json.dumps(context, ensure_ascii=False), state or row["state"], str(title).strip() if title is not None else row["title"], now_ms,
                                now_ms if state == "resolved" else row["resolved_at_ms"], work_item_id))
            row = self._conn.execute("SELECT * FROM agent_work_items WHERE work_item_id=?", (work_item_id,)).fetchone()
            return self._discussion_event_locked(row, now_ms)

    def _observe_discussion_revision_locked(self, meeting_id: str, segment_id: str, now_ms: int) -> None:
        current = self._current_discussion_locked(meeting_id)
        if current is None:
            return
        used = self._conn.execute("SELECT 1 FROM agent_work_item_evidence WHERE work_item_id=? AND segment_id=? LIMIT 1", (current["work_item_id"], segment_id)).fetchone()
        if used is None:
            return
        context = json.loads(current["context_json"] or "{}")
        # Canonical correction invalidates an interpretation of that source,
        # including a previously answered point. History remains in outbox.
        for field in ("constraints", "open_points", "responded_points", "proposals"):
            context[field] = [item for item in context.get(field, [])
                              if not isinstance(item, dict) or (segment_id not in item.get("source_refs", [])
                                  and not any(binding.get("segment_id") == segment_id
                                      for binding in (item.get("resolution_proof") or {}).get("question_evidence", [])
                                      if isinstance(binding, Mapping)))]
        context["current_solution"] = self._current_candidate_solution(context)
        context["analysis_basis_revision"] = int(context.get("analysis_basis_revision") or 1) + 1
        context["phase"] = "needs_review"
        context["last_semantic_seq"] = 0
        _invalidate_current_attention(context)
        clock = _reanalysis_clock(context)
        context["reanalysis"] = {"first_revision_at_ms": clock["first_revision_at_ms"] if clock else now_ms,
            "last_revision_at_ms": max(clock["last_revision_at_ms"], now_ms) if clock else now_ms}
        self._cancel_discussion_jobs_locked(meeting_id, current["work_item_id"], now_ms, "discussion_evidence_revised")
        self._conn.execute("UPDATE agent_work_items SET context_json=?,version=version+1,updated_at_ms=? WHERE work_item_id=?", (json.dumps(context, ensure_ascii=False), now_ms, current["work_item_id"]))
        row = self._conn.execute("SELECT * FROM agent_work_items WHERE work_item_id=?", (current["work_item_id"],)).fetchone()
        self._discussion_event_locked(row, now_ms)

    @staticmethod
    def _current_candidate_solution(context: Mapping[str, Any]) -> str:
        for point in reversed(context.get("constraints", [])):
            if (isinstance(point, dict) and point.get("kind") == "decision_adopted"
                    and point.get("status") == "adopted" and point.get("is_current_solution")
                    and point.get("source_refs")):
                return f"已明确采用的方案：{point['text']}"[:1_000]
            if (isinstance(point, dict) and point.get("kind") == "decision_candidate"
                    and point.get("status") == "candidate" and point.get("is_current_solution")
                    and point.get("source_refs")):
                return f"候选方案（尚未宣告采用）：{point['text']}"[:1_000]
        return ""

    def _complete_discussion_locked(self, *, meeting_id: str, job_id: str, metadata: Mapping[str, Any],
                                    decision: dict[str, Any], intervention: dict[str, Any] | None, now_ms: int, current_seq: int) -> dict[str, Any]:
        row = self._current_discussion_locked(meeting_id)
        if row is None:
            raise RuntimeError("discussion topic superseded")
        context = json.loads(row["context_json"] or "{}")
        context["last_checked_seq"] = max(int(context.get("last_checked_seq") or 0), int(metadata.get("anchor_seq") or 0))
        if intervention is not None:
            proposal = {"decision_id": decision.get("decision_id"), "say_this": str(intervention.get("say_this") or intervention.get("recommendation") or "")[:300],
                        "source_refs": list(intervention.get("evidence_segment_ids") or [])[:4], "kind": "ai_proposal"}
            context["proposals"] = [item for item in context.get("proposals", []) if isinstance(item, dict) and item.get("say_this") != proposal["say_this"]][-1:] + [proposal]
            while context["proposals"] and len(json.dumps(context, ensure_ascii=False)) > 6_000:
                context["proposals"].pop(0)
        self._conn.execute("UPDATE agent_work_items SET state=?,latest_decision_id=?,latest_run_id=?,trigger_job_id=?,last_checked_seq=?,last_execution_status=?,version=version+1,updated_at_ms=?,context_json=? WHERE work_item_id=?",
                           ("ready" if intervention is not None else "investigating", decision.get("decision_id"), decision.get("run_id"), job_id,
                            context["last_checked_seq"], decision.get("status") or "no_change", now_ms, json.dumps(context, ensure_ascii=False), row["work_item_id"]))
        for payload in (decision, intervention):
            if payload is not None:
                payload.update(coach_profile="participation", analysis_kind="participation", topic_id=metadata["topic_id"],
                               analysis_basis_revision=metadata["analysis_basis_revision"], proposal_type=payload.get("proposal_type") or "professional_proposal")
        if intervention is not None:
            decision.update(supporting_facts=intervention.get("supporting_facts") or [], assumptions=intervention.get("assumptions") or [])
        row = self._conn.execute("SELECT * FROM agent_work_items WHERE work_item_id=?", (row["work_item_id"],)).fetchone()
        return self._discussion_event_locked(row, now_ms)

    def _freeze_participation_locked(self, *, meeting_id: str, now_ms: int, intent: str, user_request: str,
                                     topic_id: str | None, expected_basis_revision: int | None,
                                     selected_segment_ids: Sequence[str], source_evidence: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        from .user_memory import memory_context

        row = self._ensure_discussion_locked(meeting_id, now_ms=now_ms, topic_id=topic_id)
        context = json.loads(row["context_json"] or "{}")
        basis = int(context.get("analysis_basis_revision") or 1)
        if expected_basis_revision is not None and (type(expected_basis_revision) is not int or expected_basis_revision != basis):
            raise RuntimeError("discussion basis conflict")
        ids = list(dict.fromkeys(selected_segment_ids))
        if len(ids) > 16 or any(not isinstance(item, str) or not item.strip() or len(item) > 240 for item in ids):
            raise ValueError("selected_segment_ids must be a bounded array")
        evidence_rows = self._conn.execute("SELECT * FROM transcript_segments WHERE meeting_id=? AND duplicate_of_segment_id IS NULL ORDER BY transcript_seq DESC LIMIT 12", (meeting_id,)).fetchall()
        if not ids and _attention_ready(context):
            attention = context["current_attention"]
            opportunity_ids = attention["contribution_opportunity"]["evidence_segment_ids"]
            if len(opportunity_ids) > 4:
                raise ValueError("discussion opportunity exceeds source budget")
            bound = {item["segment_id"]: item for item in attention["source_bindings"]}
            present = {item["segment_id"] for item in evidence_rows}
            for source_id in opportunity_ids:
                if source_id in present:
                    continue
                source = self._conn.execute("SELECT * FROM transcript_segments WHERE meeting_id=? AND segment_id=? "
                    "AND duplicate_of_segment_id IS NULL", (meeting_id, source_id)).fetchone()
                snapshot = bound.get(source_id) or {}
                if source is None or (int(source["revision"]), source["evidence_hash"]) != (snapshot.get("revision"), snapshot.get("evidence_hash")):
                    raise ValueError("discussion opportunity source superseded")
                evidence_rows.append(source)
                present.add(source_id)
            # Old support is visible once in the ordinary source catalog, not
            # merely repeated as a quote in working state. It is not latest.
            evidence_rows.sort(key=lambda item: int(item["transcript_seq"]))
        if ids:
            evidence_rows = self._conn.execute("SELECT * FROM transcript_segments WHERE meeting_id=? AND segment_id IN (" + ",".join("?" for _ in ids) + ") AND duplicate_of_segment_id IS NULL ORDER BY transcript_seq", (meeting_id, *ids)).fetchall()
            if len(evidence_rows) != len(ids):
                raise ValueError("selected transcript evidence does not belong to this meeting")
            # Selection supplements the current moment; the executor's anchor
            # must be frozen too, or its facts could escape the revision fence.
            latest = self._conn.execute("SELECT * FROM transcript_segments WHERE meeting_id=? AND duplicate_of_segment_id IS NULL ORDER BY transcript_seq DESC LIMIT 1", (meeting_id,)).fetchone()
            if latest is not None and latest["segment_id"] not in ids:
                evidence_rows = [*evidence_rows, latest]
        metadata = {"profile": "participation", "intent": intent, "topic_id": row["work_item_id"], "analysis_basis_revision": basis,
                    "discussion_context": context, "input_context": json.loads(row["input_context_json"] or "{}"),
                    "user_memory": memory_context(self._conn, meeting_id, "general"), "source_evidence": list(source_evidence),
                    "anchor_seq": max(int(item["transcript_seq"]) for item in evidence_rows),
                    "evidence": [{"segment_id": item["segment_id"], "revision": int(item["revision"]), "evidence_hash": item["evidence_hash"]} for item in evidence_rows]}
        metadata["material_context"] = self.get_material_context(meeting_id, provided_evidence=source_evidence)
        if "current_attention" in context:
            metadata["current_attention"] = json.loads(json.dumps(context["current_attention"], ensure_ascii=False))
        self._bound_participation_packet(metadata)
        for item in evidence_rows:
            self._conn.execute("INSERT OR IGNORE INTO agent_work_item_evidence(work_item_id,meeting_id,segment_id,revision,relation,quote,observed_at_ms) VALUES(?,?,?,?, 'supporting',?,?)",
                               (row["work_item_id"], meeting_id, item["segment_id"], int(item["revision"]), str(item["normalized_text"] or item["text"])[:900], now_ms))
        return metadata

    @staticmethod
    def _bound_participation_packet(metadata: dict[str, Any]) -> None:
        """Bound the final queued snapshot, including enqueue-only identity."""
        # Working/navigation fields can shrink, source quotes retain exact text.
        metadata.pop("packet_hash", None)
        def wire_bytes():
            return len(json.dumps(metadata, ensure_ascii=False, allow_nan=False).encode("utf-8"))
        # Leave room for the fixed hash field added below.
        budget = 47_900
        if wire_bytes() > budget:
            memory = metadata["user_memory"]
            memory["hotwords"] = memory.get("hotwords", [])[:20]
            memory["terms"] = memory.get("terms", [])[:20]
            for item in metadata["material_context"].get("attachment_catalog", []):
                item["sections"] = item.get("sections", [])[:1]
                warnings = item.get("warnings") or []
                item["omitted_warning_count"] = max(0, len(warnings) - 3)
                item["warnings"] = warnings[:3]
            metadata["navigation_truncated"] = True
        for field in ("alternatives", "proposals", "constraints", "open_points"):
            while wire_bytes() > budget and metadata["discussion_context"].get(field):
                metadata["discussion_context"][field].pop(0)
                metadata["working_state_truncated"] = True
        if wire_bytes() > budget:
            raise ValueError("本场背景超出即时上下文预算，请缩小资料范围后重试")
        metadata["packet_hash"] = hashlib.sha256(json.dumps(metadata, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        if wire_bytes() > 48_000:
            raise ValueError("本场背景超出即时上下文预算，请缩小资料范围后重试")

    def _merge_discussion_changes_locked(self, *, row, context, changes, now_ms: int) -> bool:
        """Merge source-validated points into their host-selected topic only."""
        meeting_id = row["meeting_id"]
        decision_changed = False
        for change in changes:
            point = {"id": str(change.get("item_id") or "")[:240], "text": str(change.get("content") or "")[:500],
                     "source_refs": list(change.get("evidence_segment_ids") or [])[:8], "entity_type": change.get("type")}
            if change.get("type") == "decision":
                decision_changed = True
                inactive = (change.get("operation") == "resolve" or str(change.get("status") or "").lower()
                            in {"done", "resolved", "superseded", "cancelled", "discarded", "rejected", "withdrawn"})
                status = str(change.get("status") or "").lower()
                confirmed_fact = status == "confirmed"
                adopted = status in {"adopted", "accepted", "approved", "agreed", "decided"}
                point.update(kind="decision_fact" if confirmed_fact else "decision_adopted" if adopted else "decision_candidate",
                             status="inactive" if inactive else "confirmed" if confirmed_fact else "adopted" if adopted else "candidate",
                             is_current_solution=not inactive and not confirmed_fact)
                if point["is_current_solution"]:
                    context["constraints"] = [{**item, "is_current_solution": False} if isinstance(item, dict) else item
                                              for item in context.get("constraints", [])]
            # Store the host revisions that actually supported interpretation,
            # never a document ID in the transcript evidence relation.
            snapshots = []
            for source_id in point["source_refs"]:
                source = self._conn.execute("SELECT * FROM transcript_segments WHERE meeting_id=? AND segment_id=? AND duplicate_of_segment_id IS NULL", (meeting_id, source_id)).fetchone()
                if source is None:
                    from .discussion_state_diagnostics import DiscussionProjectionRejection
                    raise DiscussionProjectionRejection("transcript_reference_missing")
                snapshots.append({"segment_id": source_id, "revision": int(source["revision"]), "evidence_hash": source["evidence_hash"]})
                self._conn.execute("INSERT OR IGNORE INTO agent_work_item_evidence(work_item_id,meeting_id,segment_id,revision,relation,quote,observed_at_ms) VALUES(?,?,?,?, 'supporting',?,?)",
                                   (row["work_item_id"], meeting_id, source_id, int(source["revision"]), str(change.get("evidence_quote") or "")[:900], now_ms))
            point["evidence"] = snapshots
            if change.get("type") == "open_question":
                point["evidence_quote"] = str(change.get("evidence_quote") or "")[:900]
                if isinstance(change.get("resolution_proof"), Mapping):
                    point["resolution_proof"] = dict(change["resolution_proof"])
            answered = change.get("operation") == "resolve" or (
                change.get("type") == "open_question" and str(change.get("status") or "").lower()
                in {"answered", "resolved", "closed", "done"}
            )
            if answered:
                context["open_points"] = [item for item in context.get("open_points", []) if not isinstance(item, dict) or item.get("id") != point["id"]]
                if change.get("type") == "decision":
                    context["constraints"] = [item for item in context.get("constraints", []) if not isinstance(item, dict) or item.get("id") != point["id"]]
                context["responded_points"] = [item for item in context.get("responded_points", []) if not isinstance(item, dict) or item.get("id") != point["id"]][-7:] + [point]
            else:
                field = "constraints" if change.get("type") == "decision" else "open_points"
                context[field] = [item for item in context.get(field, []) if not isinstance(item, dict) or item.get("id") != point["id"]][-7:] + [point]
        if decision_changed:
            context["current_solution"] = self._current_candidate_solution(context)
        return decision_changed

    def _filter_question_closures_locked(self, *, meeting_id: str,
                                        changes: Sequence[Mapping[str, Any]],
                                        topic_update: Mapping[str, Any] | None = None,
                                        state_request_metadata: Mapping[str, Any] | None = None):
        """Keep proven closures only, before any entity/outbox write in this transaction.

        This proves identity and chronology, not natural-language entailment.
        The quote's offsets refer to compact/casefold canonical text, matching
        the existing verbatim-quote normalization. Ambiguous occurrences are
        deliberately left open; no guessed offsets or extra model calls.
        """
        messages = {
            "question_not_open_or_paired": "未找到对应的未决问题或本批先提出的问题，暂不标为已回应。",
            "question_evidence_unverifiable": "原问题的引文或文本版本无法核对，问题保持未决。",
            "answer_evidence_unverifiable": "答复未能对应本轮冻结原文，问题保持未决。",
            "question_answer_quote_ambiguous": "问答引文重复，无法确定先后位置，问题保持未决。",
            "answer_not_after_question": "未能证明答复晚于提问，问题保持未决。",
            "cross_topic_requires_explicit_resolve": "跨题旧问题须以原问题ID明确resolve，终态status不能替代归属核对。",
        }
        row = self._current_discussion_locked(meeting_id)
        context = json.loads(row["context_json"] or "{}") if row else {}
        questions = {point["id"]: dict(point) for point in context.get("open_points", [])
            if isinstance(point, Mapping) and point.get("id") and point.get("entity_type") == "open_question"}
        old_question_ids = set(questions)
        previous_title = str((self._meeting_state_locked(meeting_id).get("current_topic") or {}).get("text") or "")
        later_topic_source = any(int(source["transcript_seq"]) > int(context.get("last_checked_seq") or 0)
            for source in self._quote_sources_locked(meeting_id, topic_update or {}))
        switches_topic = bool(row and topic_update and topic_update.get("operation") == "add"
            and ((previous_title and previous_title != str(topic_update.get("title") or ""))
                 or (not previous_title and later_topic_source)))
        metadata = state_request_metadata or {}
        frozen = {item["segment_id"]: (item.get("revision"), item.get("evidence_hash"))
            for item in metadata.get("evidence") or [] if isinstance(item, Mapping) and item.get("segment_id")}

        def snapshots(source_ids):
            result = []
            for source_id in source_ids:
                source = self._conn.execute("SELECT * FROM transcript_segments WHERE meeting_id=? AND segment_id=? AND duplicate_of_segment_id IS NULL",
                    (meeting_id, source_id)).fetchone()
                if source is None or (state_request_metadata is not None
                        and frozen.get(source_id) != (int(source["revision"]), source["evidence_hash"])):
                    return None
                result.append({"segment_id": source_id, "revision": int(source["revision"]), "evidence_hash": source["evidence_hash"]})
            return result

        def spans(point):
            quote = "".join(str(point.get("evidence_quote") or "").split()).casefold()
            evidence = point.get("evidence") or []
            if not quote or not evidence:
                return [], False
            matches = []
            for binding in evidence:
                if not isinstance(binding, Mapping):
                    return [], False
                source = self._conn.execute("SELECT * FROM transcript_segments WHERE meeting_id=? AND segment_id=? AND duplicate_of_segment_id IS NULL",
                    (meeting_id, binding.get("segment_id"))).fetchone()
                if (source is None or type(binding.get("revision")) is not int
                        or binding["revision"] != int(source["revision"])
                        or not binding.get("evidence_hash") or binding["evidence_hash"] != source["evidence_hash"]):
                    return [], False
                canonical = "".join(str(source["normalized_text"] or source["text"] or "").split()).casefold()
                start = canonical.find(quote)
                if start >= 0:
                    matches.append({"segment_id": source["segment_id"], "transcript_seq": int(source["transcript_seq"]),
                        "start": start, "end": start + len(quote)})
                    if canonical.find(quote, start + 1) >= 0:
                        return matches, True
            return matches, len(matches) > 1

        # Pre-11.85 points carry revision/hash but no quote. Read an existing
        # entity quote only when its exact source set matches this owner's
        # persisted question. Never infer a question from answer prose.
        for item_id, point in questions.items():
            if point.get("evidence_quote"):
                continue
            entity = self._conn.execute("SELECT text,evidence_json FROM meeting_entities WHERE meeting_id=? AND entity_id=? AND kind='open_question'",
                (meeting_id, item_id)).fetchone()
            evidence = json.loads(entity["evidence_json"] or "{}") if entity else {}
            source_ids = evidence.get("segment_ids") or ([evidence["segment_id"]] if evidence.get("segment_id") else [])
            if (entity and entity["text"] == point.get("text")
                    and set(source_ids) == set(point.get("source_refs") or []) and point.get("evidence")):
                point["evidence_quote"] = evidence.get("quote")

        accepted, rejected = [], []
        for index, raw in enumerate(changes):
            change = {key: value for key, value in raw.items() if key != "resolution_proof"}
            change.update(type=str(change.get("type") or "").strip(),
                operation=str(change.get("operation") or "").strip(),
                item_id=str(change.get("item_id") or "").strip())
            is_question = change.get("type") == "open_question"
            closing = is_question and (change.get("operation") == "resolve"
                or str(change.get("status") or "").strip().lower() in {"answered", "resolved", "closed", "done"})
            item_id = str(change.get("item_id") or "")
            if not closing:
                accepted.append(change)
                if is_question and change.get("operation") == "add":
                    questions[item_id] = {"evidence_quote": change.get("evidence_quote"),
                        "evidence": snapshots(change.get("evidence_segment_ids") or [])}
                continue
            question = questions.get(item_id)
            rule = None
            q_matches, q_ambiguous = spans(question or {})
            answer_evidence = snapshots(change.get("evidence_segment_ids") or [])
            a_matches, a_ambiguous = spans({"evidence_quote": change.get("evidence_quote"), "evidence": answer_evidence})
            if question is None:
                rule = "question_not_open_or_paired"
            elif switches_topic and item_id in old_question_ids and change.get("operation") != "resolve":
                # Preserve the existing cross-topic identity rule. Do not
                # turn a status alias into permission to update another owner.
                rule = "cross_topic_requires_explicit_resolve"
            elif not q_matches:
                rule = "question_evidence_unverifiable"
            elif answer_evidence is None or not a_matches:
                rule = "answer_evidence_unverifiable"
            elif q_ambiguous or a_ambiguous:
                rule = "question_answer_quote_ambiguous"
            elif not (a_matches[0]["transcript_seq"] > q_matches[0]["transcript_seq"]
                    or (a_matches[0]["segment_id"] == q_matches[0]["segment_id"]
                        and a_matches[0]["start"] >= q_matches[0]["end"])):
                rule = "answer_not_after_question"
            if rule:
                rejected.append({"index": index, "type": "open_question", "operation": change.get("operation"),
                    "item_id": item_id[:240], "rule": rule, "message": messages[rule]})
                continue
            change["operation"] = "resolve"
            change["status"] = "answered"
            change["resolution_proof"] = {"question_quote": str(question["evidence_quote"])[:900],
                "question_evidence": question["evidence"], "answer_evidence": answer_evidence,
                "question_position": q_matches[0], "answer_position": a_matches[0],
                "offset_unit": "compact_casefold_codepoints"}
            accepted.append(change)
            questions.pop(item_id, None)
        return accepted, {"schema_version": "discussion_state_projection.v1",
            "accepted_operations": accepted, "accepted_operation_count": len(accepted),
            "rejected_operations": rejected[:8], "rejected_operation_count": len(rejected),
            "rejections_truncated": len(rejected) > 8}

    def _quote_sources_locked(self, meeting_id: str, item: Mapping[str, Any]) -> list[Any]:
        quote = "".join(str(item.get("evidence_quote") or "").split()).casefold()
        sources = []
        for source_id in item.get("evidence_segment_ids") or []:
            source = self._conn.execute("SELECT * FROM transcript_segments WHERE meeting_id=? AND segment_id=? AND duplicate_of_segment_id IS NULL", (meeting_id, source_id)).fetchone()
            if source is not None and quote and any(quote in "".join(str(source[field] or "").split()).casefold() for field in ("normalized_text", "text")):
                sources.append(source)
        return sources

    def _trim_semantic_context(self, context, decision_changed: bool):
        for field in ("constraints", "open_points", "responded_points"):
            context[field] = context.get(field, [])[-4:]
        while len(json.dumps(context, ensure_ascii=False)) > 6_000:
            largest = max(("constraints", "open_points", "responded_points"), key=lambda key: len(context[key]))
            if not context[largest]:
                break
            context[largest].pop(0)
            if decision_changed:
                context["current_solution"] = self._current_candidate_solution(context)
        return bounded_discussion_context(context)

    def _observe_discussion_semantics_locked(self, *, meeting_id: str, topic_update: Mapping[str, Any] | None,
                                             state_changes: Sequence[Mapping[str, Any]], previous_topic_title: str,
                                             current_seq: int, now_ms: int, excluding_job_id: str | None = None,
                                             state_request_metadata: Mapping[str, Any] | None = None,
                                             state_attention=None) -> None:
        """Project already validated interpretation; this adds no model call."""
        if not topic_update and not state_changes:
            return
        row = self._current_discussion_locked(meeting_id)
        title = str((topic_update or {}).get("title") or "")
        existing_context = json.loads(row["context_json"] or "{}") if row is not None else {}
        previous = None
        current_changes = list(state_changes)
        # A user contribution can precede the first formal topic entity. In
        # that case a source-bound add on later speech is still a topic switch.
        # Naming the same initial speech must retain its provisional identity.
        later_topic_source = False
        for source_id in (topic_update or {}).get("evidence_segment_ids") or []:
            source = self._conn.execute("SELECT transcript_seq FROM transcript_segments WHERE meeting_id=? AND segment_id=?", (meeting_id, source_id)).fetchone()
            later_topic_source |= source is not None and int(source["transcript_seq"]) > int(existing_context.get("last_checked_seq") or 0)
        topic_added = bool(topic_update and topic_update.get("operation") == "add")
        if row is not None and topic_added and (
            previous_topic_title and previous_topic_title != title or not previous_topic_title and later_topic_source
        ):
            self._cancel_discussion_jobs_locked(meeting_id, row["work_item_id"], now_ms, "discussion_topic_changed", excluding_job_id=excluding_job_id)
            self._conn.execute("UPDATE agent_work_items SET state='superseded',version=version+1,updated_at_ms=? WHERE work_item_id=?", (now_ms, row["work_item_id"]))
            previous = self._conn.execute("SELECT * FROM agent_work_items WHERE work_item_id=?", (row["work_item_id"],)).fetchone()
            self._discussion_event_locked(previous, now_ms)
            row = None
        if previous is not None:
            topic_sources = self._quote_sources_locked(meeting_id, topic_update or {})
            boundary = min((int(source["transcript_seq"]) for source in topic_sources), default=current_seq)
            current_changes = []
            historical_changes = []
            old_context = json.loads(previous["context_json"] or "{}")
            old_question_ids = {
                point["id"] for point in old_context.get("open_points", [])
                if isinstance(point, dict) and point.get("id") and point.get("entity_type") == "open_question"
            }
            # An accepted add->resolve pair may occur before the next topic
            # in this same frozen batch. Keep both on the old owner rather
            # than rejecting its answer or inventing a new-topic response.
            if state_request_metadata is not None:
                old_question_ids.update(change.get("item_id") for change in state_changes
                    if change.get("type") == "open_question" and change.get("operation") == "add"
                    and (sources := self._quote_sources_locked(meeting_id, change))
                    and all(int(source["transcript_seq"]) < boundary for source in sources))
            # Only the state consumer supplies this already validated, durable
            # input snapshot. A response may precede the new topic in that same
            # pending batch; a mutable checked cursor cannot prove its freshness.
            frozen_evidence = {}
            metadata = state_request_metadata or {}
            semantic_from = metadata.get("semantic_from_seq")
            anchor = metadata.get("anchor_seq")
            if (metadata.get("profile") == "participation" and metadata.get("task_mode") == "discussion_state"
                    and metadata.get("topic_id") == previous["work_item_id"]
                    and type(semantic_from) is int and type(anchor) is int
                    and 0 <= semantic_from < anchor == current_seq):
                frozen_evidence = {item["segment_id"]: (item.get("revision"), item.get("evidence_hash"))
                    for item in metadata.get("evidence") or []
                    if isinstance(item, Mapping) and isinstance(item.get("segment_id"), str)
                    and type(item.get("revision")) is int and item["revision"] > 0
                    and isinstance(item.get("evidence_hash"), str) and item["evidence_hash"]}
            for change in state_changes:
                supporting = self._quote_sources_locked(meeting_id, change)
                if not supporting:
                    from .discussion_state_diagnostics import DiscussionProjectionRejection
                    raise DiscussionProjectionRejection("topic_quote_unverifiable")
                current_sources = [source for source in supporting if int(source["transcript_seq"]) >= boundary]
                scoped = {**change, "evidence_segment_ids": [source["segment_id"] for source in current_sources or supporting]}
                if change.get("item_id") in old_question_ids and (
                    current_sources or change.get("operation") == "resolve"
                ):
                    response_sources = current_sources if state_request_metadata is None else [
                        source for source in supporting
                        if frozen_evidence.get(source["segment_id"]) == (int(source["revision"]), source["evidence_hash"])
                        and semantic_from < int(source["transcript_seq"]) <= anchor
                    ]
                    # The new utterance can answer an existing old-topic question
                    # before changing topic. Identity selects the owner; its fresh
                    # exact quote remains the evidence for the response itself.
                    if change.get("type") != "open_question" or change.get("operation") != "resolve" or not response_sources:
                        from .discussion_state_diagnostics import DiscussionProjectionRejection
                        raise DiscussionProjectionRejection("cross_topic_old_question_operation_invalid")
                    historical_changes.append({**change, "evidence_segment_ids": [source["segment_id"] for source in response_sources]})
                    continue
                if (not current_sources and change.get("type") == "open_question"
                        and (change.get("operation") == "resolve" or str(change.get("status") or "").lower()
                             in {"answered", "resolved", "closed", "done"})):
                    from .discussion_state_diagnostics import DiscussionProjectionRejection
                    raise DiscussionProjectionRejection("cross_topic_response_owner_or_evidence_invalid")
                (current_changes if current_sources else historical_changes).append(scoped)
            # Unresolved does not mean current. A newly noticed old issue is
            # preserved on its closed topic rather than copied into the new one.
            old_changed = self._merge_discussion_changes_locked(row=previous, context=old_context, changes=historical_changes, now_ms=now_ms)
            old_context["phase"] = "closed"
            old_context = self._trim_semantic_context(old_context, old_changed)
            self._conn.execute("UPDATE agent_work_items SET context_json=?,version=version+1,updated_at_ms=? WHERE work_item_id=?",
                               (json.dumps(old_context, ensure_ascii=False), now_ms, previous["work_item_id"]))
            self._discussion_event_locked(self._conn.execute("SELECT * FROM agent_work_items WHERE work_item_id=?", (previous["work_item_id"],)).fetchone(), now_ms)
        created = row is None
        row = row or self._ensure_discussion_locked(meeting_id, now_ms=now_ms)
        context = json.loads(row["context_json"] or "{}")
        before = dict(context)
        if topic_update:
            context["current_problem"] = title[:800]
            for source_id in topic_update.get("evidence_segment_ids") or []:
                source = self._conn.execute("SELECT * FROM transcript_segments WHERE meeting_id=? AND segment_id=? AND duplicate_of_segment_id IS NULL", (meeting_id, source_id)).fetchone()
                if source is not None:
                    self._conn.execute("INSERT OR IGNORE INTO agent_work_item_evidence(work_item_id,meeting_id,segment_id,revision,relation,quote,observed_at_ms) VALUES(?,?,?,?, 'supporting',?,?)",
                                       (row["work_item_id"], meeting_id, source_id, source["revision"], str(topic_update.get("evidence_quote") or "")[:900], now_ms))
        decision_changed = self._merge_discussion_changes_locked(row=row, context=context, changes=current_changes, now_ms=now_ms)
        changed = context != before
        if not changed and not created:
            return
        def meaning(state):
            return [state.get("current_problem", ""), state.get("current_solution", ""),
                    [[(item.get("text"), item.get("status"), item.get("is_current_solution")) if isinstance(item, dict) else str(item)
                      for item in state.get(field, [])] for field in ("constraints", "open_points", "responded_points")]]
        material_change = created or meaning(context) != meaning(before)
        if material_change and not created:
            retained = self._continuing_coach_card_locked(meeting_id=meeting_id, topic_id=row["work_item_id"],
                context=before, attention=state_attention, metadata=state_request_metadata, now_ms=now_ms)
            context["analysis_basis_revision"] = int(context.get("analysis_basis_revision") or 1) + 1
            self._cancel_discussion_jobs_locked(meeting_id, row["work_item_id"], now_ms, "discussion_constraints_changed",
                excluding_job_id=excluding_job_id, retained_decision_ids=retained)
        context["last_checked_seq"] = current_seq
        if material_change:
            context["opportunity_formed_at_ms"] = now_ms
        # Bound deterministically rather than adding a compaction model call.
        context = self._trim_semantic_context(context, decision_changed)
        self._conn.execute("UPDATE agent_work_items SET context_json=?,title=?,summary=?,last_checked_seq=?,version=version+1,updated_at_ms=? WHERE work_item_id=?",
                           (json.dumps(context, ensure_ascii=False), title[:80] or row["title"], context["current_problem"], current_seq, now_ms, row["work_item_id"]))
        row = self._conn.execute("SELECT * FROM agent_work_items WHERE work_item_id=?", (row["work_item_id"],)).fetchone()
        self._discussion_event_locked(row, now_ms)

    def _mark_discussion_semantic_checked_locked(self, meeting_id: str, seq: int, now_ms: int) -> None:
        row = self._current_discussion_locked(meeting_id)
        if row is None:
            return
        context = json.loads(row["context_json"] or "{}")
        context["last_semantic_seq"] = max(int(context.get("last_semantic_seq") or 0), seq)
        context["phase"] = "discussing"
        context.pop("reanalysis", None)
        self._conn.execute("UPDATE agent_work_items SET context_json=?,version=version+1,updated_at_ms=? WHERE work_item_id=?",
                           (json.dumps(context, ensure_ascii=False), now_ms, row["work_item_id"]))
        self._discussion_event_locked(self._current_discussion_locked(meeting_id), now_ms)

    def _validated_attention_locked(self, meeting_id: str, attention, metadata: Mapping[str, Any]):
        from .discussion_attention import validate_current_attention
        from .realtime_intelligence import IntelligenceResponseValidationError

        required = metadata.get("attention_contract") == _ATTENTION_CONTRACT
        if attention is None and not required:
            return None
        if not required:
            raise IntelligenceResponseValidationError("unregistered attention contract")
        paragraphs = {}
        for frozen in metadata.get("evidence") or []:
            source = self._conn.execute("SELECT * FROM transcript_segments WHERE meeting_id=? AND segment_id=? "
                "AND duplicate_of_segment_id IS NULL", (meeting_id, frozen.get("segment_id"))).fetchone()
            if source is None or (int(source["revision"]), source["evidence_hash"]) != (frozen.get("revision"), frozen.get("evidence_hash")):
                raise IntelligenceResponseValidationError("attention frozen evidence superseded", category="evidence")
            paragraphs[source["segment_id"]] = SimpleNamespace(id=source["segment_id"], text=str(source["normalized_text"] or source["text"] or ""))
        return validate_current_attention(attention, paragraphs_by_id=paragraphs, metadata=metadata, required=True)

    def _attention_topic_update_locked(self, *, meeting_id: str, topic_update, attention,
                                       metadata: Mapping[str, Any]):
        """Use model relationship without letting an interlude rename history."""
        validated = self._validated_attention_locked(meeting_id, attention, metadata)
        if validated is None or not topic_update or topic_update.get("operation") == "noop":
            return topic_update
        row = self._current_discussion_locked(meeting_id)
        context = json.loads(row["context_json"] or "{}") if row else {}
        canonical_topic = self._meeting_state_locked(meeting_id).get("current_topic") or {}
        mature = self._discussion_is_mature_locked(meeting_id, row, context)
        relation = validated["current_focus"]["relation"]
        if not mature or relation == "changed":
            return topic_update
        if relation != "same_topic":
            return None
        # Same-topic updates retain the established name. The new observation
        # and opportunity are stored independently, including on a core noop.
        return {**topic_update, "operation": "update", "title": str(canonical_topic.get("text") or context.get("current_problem") or topic_update.get("title"))}

    def _discussion_is_mature_locked(self, meeting_id: str, row, context: Mapping[str, Any]) -> bool:
        """Revision cursor zero is not proof that old understood history is new."""
        if row is None:
            return False
        if int(context.get("last_semantic_seq") or 0) > 0 or self._meeting_state_locked(meeting_id).get("current_topic"):
            return True
        return self._conn.execute("SELECT 1 FROM jobs WHERE meeting_id=? AND kind='intelligence' AND status='succeeded' "
            "AND json_extract(request_context_json,'$.task_mode')='discussion_state' "
            "AND (json_extract(request_context_json,'$.topic_id')=? OR json_extract(output_json,'$.discussion_identity.topic_id')=?) LIMIT 1",
            (meeting_id, row["work_item_id"], row["work_item_id"])).fetchone() is not None

    def _cancel_automatic_attention_locked(self, meeting_id: str, topic_id: str, now_ms: int,
                                          *, excluding_job_id: str | None = None) -> None:
        rows = self._conn.execute("SELECT id,request_context_json FROM jobs WHERE meeting_id=? AND kind='intelligence' "
            "AND trigger_type IN ('discussion_update','task_due','answer_ready') "
            "AND status IN ('pending','retry_wait','running')", (meeting_id,)).fetchall()
        for job in rows:
            packet = json.loads(job["request_context_json"] or "{}")
            if (job["id"] == excluding_job_id or packet.get("profile") != "participation"
                    or packet.get("task_mode") == "discussion_state" or packet.get("topic_id") != topic_id):
                continue
            self._conn.execute("UPDATE jobs SET status='cancelled',lease_owner=NULL,lease_until_ms=NULL,"
                "error_class='discussion_attention_changed',completed_at_ms=?,updated_at_ms=? WHERE id=?", (now_ms, now_ms, job["id"]))
        for binding in self._conn.execute("SELECT b.*,j.trigger_type FROM coach_source_bindings b JOIN jobs j ON j.id=b.job_id "
            "WHERE b.meeting_id=? AND b.invalidated_at_ms IS NULL AND j.trigger_type IN ('discussion_update','task_due','answer_ready')",
            (meeting_id,)).fetchall():
            decision = json.loads(binding["payload_json"] or "{}")
            if decision.get("topic_id") != topic_id:
                continue
            self._conn.execute("UPDATE coach_source_bindings SET invalidated_at_ms=? WHERE meeting_id=? AND decision_id=?",
                (now_ms, meeting_id, binding["decision_id"]))
            decision.update(decision_id=f"{binding['decision_id']}:attention-stale", status="stale", status_reason="discussion_attention_changed",
                decision_reason="当前贡献机会已变化，这条建议已收束。", supersedes_decision_id=binding["decision_id"],
                lifecycle_refresh=True, lifecycle_action="retract", lifecycle_refresh_previous_decision_id=binding["decision_id"])
            self._append_event_locked(meeting_id=meeting_id, event_type="meeting.intelligence.applied", aggregate_type="intelligence",
                aggregate_id=binding["job_id"], occurred_at_ms=now_ms, idempotency_key=f"attention.retract:{binding['decision_id']}",
                payload={"job_id": binding["job_id"], "coach_decision": decision, "coach_intervention": None, "follow_up": None},
                correlation_id=meeting_id, causation_id="current_attention")

    def _apply_current_attention_locked(self, *, meeting_id: str, attention, metadata: Mapping[str, Any],
                                        now_ms: int, excluding_job_id: str | None = None):
        """Persist current observation/opportunity within the existing apply transaction."""
        value = self._validated_attention_locked(meeting_id, attention, metadata)
        if value is None:
            return None
        row = self._current_discussion_locked(meeting_id)
        if row is None:
            raise RuntimeError("attention discussion missing")
        context = json.loads(row["context_json"] or "{}")
        previous = context.get("current_attention") or {}
        revision = int(previous.get("control_revision") or 0)
        opportunity = value["contribution_opportunity"]
        source_ids = set(opportunity["evidence_segment_ids"])
        for observation in value["current_focus"]["observations"]:
            source_ids.update(observation["evidence_segment_ids"])
        for field in ("object", "setting", "purpose"):
            if value["current_focus"][field] is not None:
                source_ids.update(value["current_focus"][field]["evidence_segment_ids"])
        sources = sorted((dict(item) for item in metadata["evidence"] if item["segment_id"] in source_ids), key=lambda item: item["segment_id"])
        opportunity_id = None
        if opportunity["status"] == "present":
            inherited = opportunity.get("opportunity_id")
            if inherited is not None:
                if not _attention_ready(context) or inherited != previous.get("opportunity_id"):
                    from .realtime_intelligence import IntelligenceResponseValidationError
                    raise IntelligenceResponseValidationError("attention opportunity identity is no longer current")
                if opportunity["intent"] != previous["contribution_opportunity"]["intent"]:
                    from .realtime_intelligence import IntelligenceResponseValidationError
                    raise IntelligenceResponseValidationError("attention inherited opportunity intent changed")
                opportunity_id = inherited
            else:
                identity = {"topic_id": row["work_item_id"], "intent": opportunity["intent"],
                    "evidence_quote": opportunity["evidence_quote"],
                    "sources": [item for item in sources if item["segment_id"] in opportunity["evidence_segment_ids"]]}
                old_opportunity = previous.get("contribution_opportunity") or {}
                old_identity = {"topic_id": row["work_item_id"], "intent": old_opportunity.get("intent"),
                    "evidence_quote": old_opportunity.get("evidence_quote"),
                    "sources": [item for item in previous.get("source_bindings") or []
                        if item["segment_id"] in (old_opportunity.get("evidence_segment_ids") or [])]}
                # Reappearance after an explicit unavailable state is one new
                # opportunity; a continuous present state does not tick-rearm.
                if _attention_ready(context) and identity == old_identity:
                    opportunity_id = previous["opportunity_id"]
                else:
                    if previous and not _attention_ready(context):
                        identity["reactivation"] = revision
                    opportunity_id = "opportunity:" + hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:28]
        old_control = (previous.get("opportunity_id"), (previous.get("contribution_opportunity") or {}).get("status"))
        control = (opportunity_id, opportunity["status"])
        # A model-declared switch/interlude invalidates a continuing active
        # opportunity. Ordinary updated quotes/coverage and reason do not.
        previous_relation = (previous.get("current_focus") or {}).get("relation")
        relationship_changed = value["current_focus"]["relation"] in {"changed", "interlude"} and value["current_focus"]["relation"] != previous_relation
        changed = old_control != control or relationship_changed
        saved = {**value, "contract": _ATTENTION_CONTRACT, "control_revision": revision + 1 if changed or revision == 0 else revision,
            "opportunity_id": opportunity_id, "covered_seq": int(metadata["anchor_seq"]), "source_bindings": sources}
        if changed:
            self._cancel_automatic_attention_locked(meeting_id, row["work_item_id"], now_ms, excluding_job_id=excluding_job_id)
            if opportunity_id:
                context["opportunity_formed_at_ms"] = now_ms
        context["current_attention"] = saved
        # The new protected attention may be larger than the prior one. Trim
        # older optional semantic history after replacing it, under the same
        # cap; protected-only overflow still fails this atomic projection.
        context = self._trim_semantic_context(context, bool(self._current_candidate_solution(context)))
        self._conn.execute("UPDATE agent_work_items SET context_json=?,version=version+1,updated_at_ms=? WHERE work_item_id=?",
            (json.dumps(context, ensure_ascii=False), now_ms, row["work_item_id"]))
        for source in sources:
            self._conn.execute("INSERT OR IGNORE INTO agent_work_item_evidence(work_item_id,meeting_id,segment_id,revision,relation,quote,observed_at_ms) "
                "VALUES(?,?,?,?, 'supporting','',?)", (row["work_item_id"], meeting_id, source["segment_id"], source["revision"], now_ms))
        self._discussion_event_locked(self._current_discussion_locked(meeting_id), now_ms)
        return saved

    def _set_reanalysis_defer_locked(self, row, context: dict[str, Any], reason: str | None, now_ms: int) -> None:
        """Persist only a changed bounded status; scheduler ticks are not events."""
        clock = _reanalysis_clock(context)
        if clock is None or clock.get("defer_reason") == reason:
            return
        if reason is None:
            clock.pop("defer_reason", None)
        else:
            clock["defer_reason"] = reason
        context["reanalysis"] = clock
        self._conn.execute("UPDATE agent_work_items SET context_json=?,version=version+1,updated_at_ms=? WHERE work_item_id=?",
            (json.dumps(context, ensure_ascii=False), now_ms, row["work_item_id"]))
        self._discussion_event_locked(self._current_discussion_locked(row["meeting_id"]), now_ms)

    def enqueue_discussion_state(self, *, meeting_id: str, now_ms: int, cooldown_ms: int = 60_000,
                                 stable_ms: int = 2_500, minimum_segments: int = 3, minimum_characters: int = 120,
                                 tail_catchup_ms: int = 10_000) -> dict[str, Any] | None:
        """Coalesce stable unprocessed speech into one cancellable semantic read."""
        from .discussion_state_identity import state_input_fingerprint

        with self._write_transaction():
            self._raise_if_tombstoned_locked(meeting_id)
            meeting = self._conn.execute("SELECT * FROM meetings WHERE id=?", (meeting_id,)).fetchone()
            if meeting is None or meeting["state"] != "live":
                return None
            latest = self._conn.execute("SELECT * FROM transcript_segments WHERE meeting_id=? AND duplicate_of_segment_id IS NULL ORDER BY transcript_seq DESC LIMIT 1", (meeting_id,)).fetchone()
            if latest is None:
                return None
            row = self._current_discussion_locked(meeting_id)
            if row is not None:
                # Source authorization changes invalidate cards and frozen
                # jobs, but do not make understood canonical speech new again.
                row = self._ensure_discussion_locked(meeting_id, now_ms=now_ms)
            context = json.loads(row["context_json"] or "{}") if row else {}
            attention = context.get("current_attention") or {}
            # Refresh absent legacy attention, or a host-revoked snapshot after
            # background/source authorization changes. A valid none/uncertain
            # result is still understood and must not cause repeated calls.
            attention_refresh = (self._discussion_is_mature_locked(meeting_id, row, context)
                and int(context.get("last_semantic_seq") or 0) >= int(latest["transcript_seq"])
                and context.get("phase") != "needs_review"
                and (attention.get("contract") != _ATTENTION_CONTRACT
                    or not (attention.get("current_focus") or {}).get("observations") or not attention.get("source_bindings")))
            needs_review = context.get("phase") == "needs_review"
            clock = _reanalysis_clock(context) if needs_review else None
            if needs_review and clock is None and int(context.get("last_semantic_seq") or 0) == 0:
                # Upgrade old corrected working state using its actual evidence
                # timestamps, rather than starting a new 60s cooldown on read.
                revised_at = self._conn.execute("SELECT MAX(s.updated_at_ms) FROM transcript_segments s "
                    "JOIN agent_work_item_evidence e ON e.meeting_id=s.meeting_id AND e.segment_id=s.segment_id "
                    "WHERE e.work_item_id=? AND s.revision>e.revision", (row["work_item_id"],)).fetchone()[0]
                if revised_at is not None:
                    clock = {"first_revision_at_ms": int(revised_at), "last_revision_at_ms": int(revised_at)}
                    context["reanalysis"] = clock
                    self._conn.execute("UPDATE agent_work_items SET context_json=?,version=version+1,updated_at_ms=? WHERE work_item_id=?",
                        (json.dumps(context, ensure_ascii=False), now_ms, row["work_item_id"]))
                    self._discussion_event_locked(self._current_discussion_locked(meeting_id), now_ms)
            # A correction to an earlier used source starts its own settle
            # clock; the unchanged newest transcript cannot make it stable.
            settle_ms = min(max(0, stable_ms), 2_000) if clock else max(0, stable_ms)
            changed_at = max(int(latest["updated_at_ms"]), clock["last_revision_at_ms"] if clock else 0)
            if now_ms - changed_at < settle_ms:
                if clock:
                    self._set_reanalysis_defer_locked(row, context, "evidence_changing", now_ms)
                return None
            # Actual transcript revisions explicitly reset this cursor to zero.
            checked_seq = int(context.get("last_semantic_seq") or 0)
            fresh = self._conn.execute("SELECT normalized_text,text,updated_at_ms FROM transcript_segments WHERE meeting_id=? AND transcript_seq>? AND duplicate_of_segment_id IS NULL ORDER BY transcript_seq DESC LIMIT 12", (meeting_id, checked_seq)).fetchall()
            fresh_characters = sum(len(str(item["normalized_text"] or item["text"] or "")) for item in fresh)
            below_minimum = len(fresh) < minimum_segments and fresh_characters < minimum_characters
            short_tail = not needs_review and checked_seq > 0 and below_minimum and any(
                str(item["normalized_text"] or item["text"] or "").strip() for item in fresh)
            if not attention_refresh and (not fresh or not needs_review and below_minimum and not short_tail):
                return None
            if attention_refresh and self._has_pending_discussion_corrections_locked(meeting_id):
                return None
            if short_tail:
                # Formal text stability, including an earlier unprocessed
                # revision, is distinct from acoustic silence or latest seq.
                if now_ms - max(int(item["updated_at_ms"]) for item in fresh) < max(0, stable_ms, tail_catchup_ms):
                    return None
                if self._conn.execute("SELECT 1 FROM jobs WHERE meeting_id=? AND kind='correction' "
                    "AND status IN ('pending','retry_wait','running') LIMIT 1", (meeting_id,)).fetchone():
                    return None
            prior = self._conn.execute("SELECT * FROM jobs WHERE meeting_id=? AND kind='intelligence' AND json_extract(request_context_json,'$.task_mode')='discussion_state' ORDER BY created_at_ms DESC LIMIT 1", (meeting_id,)).fetchone()
            active = self._conn.execute("SELECT 1 FROM jobs WHERE meeting_id=? AND kind='intelligence' "
                "AND json_extract(request_context_json,'$.task_mode')='discussion_state' "
                "AND status IN ('pending','retry_wait','running') LIMIT 1", (meeting_id,)).fetchone()
            if active:
                if clock:
                    self._set_reanalysis_defer_locked(row, context, "background_busy", now_ms)
                return None
            # Dirty revisions and settled below-minimum tails can catch up.
            # Failed-input fingerprint admission below still applies unchanged.
            if prior and not clock and not short_tail and now_ms - int(prior["created_at_ms"]) < cooldown_ms:
                return None
            if self._conn.execute("SELECT 1 FROM jobs WHERE meeting_id=? AND kind='intelligence' AND trigger_type='user_request' AND status IN ('pending','retry_wait','running') LIMIT 1", (meeting_id,)).fetchone():
                if clock:
                    self._set_reanalysis_defer_locked(row, context, "priority_request", now_ms)
                return None
            paragraph = self._conn.execute("SELECT p.revision FROM semantic_paragraphs p JOIN semantic_paragraph_checkpoints m ON p.meeting_id=m.meeting_id AND p.paragraph_id=m.paragraph_id WHERE m.meeting_id=? AND m.checkpoint_id=?", (meeting_id, latest["segment_id"])).fetchone()
            if paragraph is None:
                return None
            if clock:
                self._set_reanalysis_defer_locked(row, context, None, now_ms)
            metadata = self._freeze_participation_locked(meeting_id=meeting_id, now_ms=now_ms, intent="freeform", user_request="",
                topic_id=row["work_item_id"] if row else None, expected_basis_revision=None, selected_segment_ids=(), source_evidence=())
            metadata["task_mode"] = "discussion_state"
            metadata["attention_contract"] = _ATTENTION_CONTRACT
            frozen_ids = [item["segment_id"] for item in metadata["evidence"]]
            focus_rows = self._conn.execute("SELECT segment_id,normalized_text,text FROM transcript_segments WHERE meeting_id=? "
                "AND segment_id IN (" + ",".join("?" for _ in frozen_ids) + ") ORDER BY transcript_seq", (meeting_id, *frozen_ids)).fetchall()
            metadata["attention_focus_segment_ids"] = [item["segment_id"] for item in focus_rows
                if str(item["normalized_text"] or item["text"] or "").strip()][-2:]
            metadata["semantic_from_seq"] = checked_seq
            fingerprint = state_input_fingerprint(metadata)
            if fingerprint is None:
                raise ValueError("discussion state input identity is invalid")
            failed_rows = self._conn.execute("SELECT request_context_json FROM jobs WHERE meeting_id=? AND kind='intelligence' "
                "AND json_extract(request_context_json,'$.task_mode')='discussion_state' "
                "AND (status='failed' OR (status='cancelled' AND error_class='deadline_exceeded'))", (meeting_id,)).fetchall()
            for failed in failed_rows:
                failed_metadata = json.loads(failed["request_context_json"] or "{}")
                previous_key = failed_metadata.get("state_input_fingerprint")
                if not isinstance(previous_key, str) or len(previous_key) != 64 or any(char not in "0123456789abcdef" for char in previous_key):
                    previous_key = state_input_fingerprint(failed_metadata)
                if previous_key == fingerprint:
                    return None
            metadata["state_input_fingerprint"] = fingerprint
            self._bound_participation_packet(metadata)
            identity = hashlib.sha256(f"{meeting_id}:{metadata['topic_id']}:{metadata['analysis_basis_revision']}:{latest['transcript_seq']}:{now_ms}".encode()).hexdigest()[:28]
            job_id = f"job_discussion_state_{identity}"
            self._conn.execute("INSERT INTO jobs(id,meeting_id,kind,status,priority,input_transcript_seq,input_version,evidence_segment_id,evidence_hash,generation_id,trigger_type,work_item_id,trigger_reference_seq,user_request,idempotency_key,attempts,max_attempts,next_attempt_at_ms,deadline_at_ms,final_committed_at_ms,created_at_ms,updated_at_ms,request_context_json) VALUES(?,?,'intelligence','pending',20,?,?,?,?,?,'discussion_update',?,?,NULL,?,0,1,?,?,?,?,?,?)",
                (job_id, meeting_id, latest["transcript_seq"], paragraph["revision"], latest["segment_id"], latest["evidence_hash"], job_id,
                 job_id, latest["transcript_seq"], job_id, now_ms, now_ms + 30_000, now_ms, now_ms, now_ms, json.dumps(metadata, ensure_ascii=False)))
            return self._job_dict(self._conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())

    def has_pending_discussion_corrections(self, meeting_id: str) -> bool:
        with self._lock:
            return self._has_pending_discussion_corrections_locked(meeting_id)

    def _has_pending_discussion_corrections_locked(self, meeting_id: str) -> bool:
        return self._conn.execute("SELECT 1 FROM jobs WHERE meeting_id=? AND kind='correction' "
            "AND status IN ('pending','running','retry_wait') LIMIT 1", (meeting_id,)).fetchone() is not None

    def discussion_revised_recovery_job_ids(self, meeting_id: str) -> list[str]:
        """Return only old cancellations proven obsolete by successful memory."""
        with self._lock:
            current = self._current_discussion_locked(meeting_id)
            if current is None or self._has_pending_discussion_corrections_locked(meeting_id):
                return []
            context = json.loads(current["context_json"] or "{}")
            previous = self._conn.execute("SELECT * FROM jobs WHERE meeting_id=? AND kind='intelligence' "
                "AND trigger_type='discussion_update' AND status='cancelled' "
                "AND error_class='discussion_evidence_revised' ORDER BY created_at_ms DESC LIMIT 24",
                (meeting_id,)).fetchall()
            return [job["id"] for job in previous
                if self._revised_automatic_recovery_locked(meeting_id, current, context, job)]

    def _revised_automatic_recovery_locked(self, meeting_id: str, current, context, previous) -> bool:
        """Only a new proven semantic version can replace a revised cancelled run."""
        if previous["status"] != "cancelled" or previous["error_class"] != "discussion_evidence_revised":
            return False
        packet = json.loads(previous["request_context_json"] or "{}")
        basis = int(context.get("analysis_basis_revision") or 1)
        if packet.get("topic_id") == current["work_item_id"] and packet.get("analysis_basis_revision") == basis:
            return False
        semantic_seq = int(context.get("last_semantic_seq") or 0)
        if semantic_seq <= 0 or context.get("phase") != "discussing":
            return False
        if self._conn.execute("SELECT 1 FROM jobs WHERE meeting_id=? AND kind='intelligence' "
            "AND json_extract(request_context_json,'$.task_mode')='discussion_state' "
            "AND status IN ('pending','running','retry_wait') LIMIT 1", (meeting_id,)).fetchone():
            return False
        state = self._conn.execute("SELECT request_context_json FROM jobs WHERE meeting_id=? AND kind='intelligence' "
            "AND json_extract(request_context_json,'$.task_mode')='discussion_state' AND status='succeeded' "
            "AND input_transcript_seq>=? AND input_transcript_seq<=? AND completed_at_ms>=? "
            "ORDER BY completed_at_ms DESC LIMIT 1",
            (meeting_id, int(previous["input_transcript_seq"]), semantic_seq,
                int(previous["completed_at_ms"] or previous["updated_at_ms"]))).fetchone()
        if state is None:
            return False

        def source_matches(evidence):
            row = self._conn.execute("SELECT revision,evidence_hash FROM transcript_segments "
                "WHERE meeting_id=? AND segment_id=? AND duplicate_of_segment_id IS NULL",
                (meeting_id, evidence.get("segment_id"))).fetchone()
            return row is not None and (int(row["revision"]), row["evidence_hash"]) == (evidence.get("revision"), evidence.get("evidence_hash"))

        checked = json.loads(state["request_context_json"] or "{}").get("evidence") or []
        return bool(checked) and all(source_matches(item) for item in checked) and any(
            not source_matches(item) for item in packet.get("evidence") or [])

    def enqueue_realtime_participation(self, *, meeting_id: str, now_ms: int, cooldown_ms: int = 30_000,
                                      stable_ms: int = 2_500, minimum_segments: int = 3,
                                      minimum_characters: int = 120,
                                      source_evidence: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any] | None:
        """Let Pi assess fresh speech directly; semantic memory is optional.

        The host admits a stable input, not a model-established opportunity.
        One bounded Pi call assesses need and contribution together. Revisions,
        material authorization and normal publication fences remain unchanged.
        """
        from .knowledge_context import source_evidence_state

        with self._write_transaction():
            self._raise_if_tombstoned_locked(meeting_id)
            meeting = self._conn.execute("SELECT state FROM meetings WHERE id=?", (meeting_id,)).fetchone()
            if meeting is None or meeting["state"] != "live":
                return None
            rows = self._conn.execute("SELECT * FROM transcript_segments WHERE meeting_id=? "
                "AND duplicate_of_segment_id IS NULL ORDER BY transcript_seq DESC LIMIT 12", (meeting_id,)).fetchall()
            rows = [item for item in rows if str(item["normalized_text"] or item["text"] or "").strip()]
            if not rows or now_ms - max(int(item["updated_at_ms"]) for item in rows) < max(0, stable_ms):
                return None
            if source_evidence and source_evidence_state(self._conn, meeting_id, source_evidence) != "valid":
                return None
            prior = self._conn.execute("SELECT * FROM jobs WHERE meeting_id=? AND kind='intelligence' "
                "AND COALESCE(json_extract(request_context_json,'$.task_mode'),'')!='discussion_state' "
                "AND trigger_type IN ('discussion_update','user_request','answer_ready','task_due') "
                "ORDER BY created_at_ms DESC LIMIT 24", (meeting_id,)).fetchall()
            if any(item["status"] in {"pending", "retry_wait", "running"} for item in prior):
                return None
            automatic = [item for item in prior if item["trigger_type"] != "user_request"]
            if any(now_ms - int(item["created_at_ms"]) < max(0, cooldown_ms) for item in automatic):
                return None
            checked_seq = max((int(item["input_transcript_seq"]) for item in automatic), default=0)
            fresh = [item for item in rows if int(item["transcript_seq"]) > checked_seq]
            below_minimum = len(fresh) < minimum_segments and sum(
                len(str(item["normalized_text"] or item["text"] or "")) for item in fresh) < minimum_characters
            if below_minimum and fresh and now_ms - max(
                    int(item["updated_at_ms"]) for item in fresh) < max(stable_ms, 10_000):
                return None
            metadata = self._freeze_participation_locked(meeting_id=meeting_id, now_ms=now_ms,
                intent="freeform", user_request=PARTICIPATION_INTENTS["freeform"], topic_id=None,
                expected_basis_revision=None, selected_segment_ids=[item["segment_id"] for item in rows],
                source_evidence=source_evidence)
            # Semantic memory changes alone do not authorize another paid call.
            fingerprint = hashlib.sha256(json.dumps({"evidence": metadata["evidence"],
                "input_context": metadata["input_context"], "source_evidence": metadata["source_evidence"]},
                ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            if self._conn.execute("SELECT 1 FROM jobs WHERE meeting_id=? AND kind='intelligence' "
                    "AND json_extract(request_context_json,'$.direct_input_fingerprint')=? LIMIT 1",
                    (meeting_id, fingerprint)).fetchone():
                return None
            metadata.pop("current_attention", None)
            metadata["discussion_context"].pop("current_attention", None)
            metadata.update(automatic_direct=True, direct_input_fingerprint=fingerprint, frozen_at_ms=now_ms,
                proposal_contract="participation_proposal.v2",
                opportunity_formed_at_ms=now_ms, current_need_contract=CURRENT_NEED_CONTRACT,
                current_need_basis={"attention_covered_seq": 0, "packet_anchor_seq": metadata["anchor_seq"],
                    "focus_segment_ids": [item["segment_id"] for item in reversed(rows[:2])],
                    "unassessed_segment_ids": [item["segment_id"] for item in reversed(rows[:2])]})
            # Do not let background memory occupy the live decision window.
            states = self._conn.execute("SELECT id FROM jobs WHERE meeting_id=? AND kind='intelligence' "
                "AND json_extract(request_context_json,'$.task_mode')='discussion_state' "
                "AND status IN ('pending','retry_wait','running')", (meeting_id,)).fetchall()
            metadata["preempted_job_ids"] = [item["id"] for item in states]
            for state in states:
                self._conn.execute("UPDATE jobs SET status='cancelled',lease_owner=NULL,lease_until_ms=NULL,"
                    "error_class='preempted_by_realtime_participation',completed_at_ms=?,updated_at_ms=? WHERE id=?",
                    (now_ms, now_ms, state["id"]))
            self._bound_participation_packet(metadata)
            anchor = rows[0]
            paragraph = self._conn.execute("SELECT p.revision FROM semantic_paragraphs p "
                "JOIN semantic_paragraph_checkpoints m ON p.meeting_id=m.meeting_id AND p.paragraph_id=m.paragraph_id "
                "WHERE m.meeting_id=? AND m.checkpoint_id=?", (meeting_id, anchor["segment_id"])).fetchone()
            if paragraph is None:
                return None
            job_id = "job_realtime_participation_" + hashlib.sha256(
                f"{meeting_id}:{fingerprint}".encode()).hexdigest()[:28]
            self._conn.execute("INSERT INTO jobs(id,meeting_id,kind,status,priority,input_transcript_seq,input_version,"
                "evidence_segment_id,evidence_hash,generation_id,trigger_type,work_item_id,trigger_reference_seq,"
                "user_request,idempotency_key,attempts,max_attempts,next_attempt_at_ms,deadline_at_ms,"
                "final_committed_at_ms,created_at_ms,updated_at_ms,request_context_json) "
                "VALUES(?,?,'intelligence','pending',90,?,?,?,?,?,'discussion_update',?,?,?,?,0,1,?,?,?,?,?,?)",
                (job_id, meeting_id, int(anchor["transcript_seq"]), int(paragraph["revision"]), anchor["segment_id"],
                 anchor["evidence_hash"], job_id, job_id, int(anchor["transcript_seq"]), PARTICIPATION_INTENTS["freeform"],
                 job_id, now_ms, now_ms + 30_000, now_ms, now_ms, now_ms, json.dumps(metadata, ensure_ascii=False)))
            return self._job_dict(self._conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())

    def enqueue_discussion_update(self, *, meeting_id: str, now_ms: int, cooldown_ms: int = 30_000,
                                  source_evidence: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any] | None:
        with self._write_transaction():
            self._raise_if_tombstoned_locked(meeting_id)
            meeting = self._conn.execute("SELECT state FROM meetings WHERE id=?", (meeting_id,)).fetchone()
            row = self._current_discussion_locked(meeting_id)
            if meeting is None or meeting["state"] != "live" or row is None:
                return None
            row = self._ensure_discussion_locked(meeting_id, now_ms=now_ms)
            context = json.loads(row["context_json"] or "{}")
            if self._has_pending_discussion_corrections_locked(meeting_id) and not _attention_ready(context):
                return None
            basis = int(context.get("analysis_basis_revision") or 1)
            if not context.get("current_problem") or context.get("phase") == "closed" or not _attention_ready(context):
                return None
            attention = context["current_attention"]
            opportunity_id = attention["opportunity_id"]
            prior = self._conn.execute("SELECT * FROM jobs WHERE meeting_id=? AND kind='intelligence' AND trigger_type='discussion_update' AND COALESCE(json_extract(request_context_json,'$.task_mode'),'')!='discussion_state' ORDER BY created_at_ms DESC LIMIT 24", (meeting_id,)).fetchall()
            for job in prior:
                packet = json.loads(job["request_context_json"] or "{}")
                if (packet.get("topic_id") == row["work_item_id"]
                        and (packet.get("current_attention") or {}).get("opportunity_id") == opportunity_id):
                    return None
            recent = [job for job in prior if now_ms - int(job["created_at_ms"]) < max(0, int(cooldown_ms))]
            if any(not self._revised_automatic_recovery_locked(meeting_id, row, context, job) for job in recent):
                return None
            if self._conn.execute("SELECT 1 FROM jobs WHERE meeting_id=? AND kind='intelligence' AND trigger_type='user_request' AND status IN ('pending','retry_wait','running') LIMIT 1", (meeting_id,)).fetchone():
                return None
            metadata = self._freeze_participation_locked(meeting_id=meeting_id, now_ms=now_ms, intent="freeform", user_request=PARTICIPATION_INTENTS["freeform"],
                                                         topic_id=row["work_item_id"], expected_basis_revision=basis, selected_segment_ids=(), source_evidence=source_evidence)
            metadata["opportunity_formed_at_ms"] = int(context.get("opportunity_formed_at_ms") or now_ms)
            metadata["automatic_attention"] = True
            frozen_ids = [item["segment_id"] for item in metadata["evidence"]]
            focus_rows = self._conn.execute("SELECT segment_id,transcript_seq,text,normalized_text FROM transcript_segments "
                "WHERE meeting_id=? AND segment_id IN (" + ",".join("?" for _ in frozen_ids) + ") "
                "AND duplicate_of_segment_id IS NULL ORDER BY transcript_seq", (meeting_id, *frozen_ids)).fetchall()
            focus_rows = [item for item in focus_rows if str(item["normalized_text"] or item["text"]).strip()][-2:]
            covered = int(metadata["current_attention"]["covered_seq"])
            metadata["current_need_contract"] = CURRENT_NEED_CONTRACT
            metadata["current_need_basis"] = {"attention_covered_seq": covered, "packet_anchor_seq": metadata["anchor_seq"],
                "focus_segment_ids": [item["segment_id"] for item in focus_rows],
                "unassessed_segment_ids": [item["segment_id"] for item in focus_rows if int(item["transcript_seq"]) > covered]}
            self._bound_participation_packet(metadata)
            identity = hashlib.sha256(f"{meeting_id}:{row['work_item_id']}:{basis}:{opportunity_id}".encode()).hexdigest()[:28]
            job_id = f"job_discussion_{identity}"
            anchor = self._conn.execute("SELECT * FROM transcript_segments WHERE meeting_id=? AND duplicate_of_segment_id IS NULL ORDER BY transcript_seq DESC LIMIT 1", (meeting_id,)).fetchone()
            paragraph = self._conn.execute("SELECT p.revision FROM semantic_paragraphs p JOIN semantic_paragraph_checkpoints m ON p.meeting_id=m.meeting_id AND p.paragraph_id=m.paragraph_id WHERE m.meeting_id=? AND m.checkpoint_id=?", (meeting_id, anchor["segment_id"])).fetchone()
            if paragraph is None:
                return None
            for job in prior:
                if job["status"] in {"pending", "retry_wait"}:
                    self._conn.execute("UPDATE jobs SET status='cancelled',error_class='discussion_coalesced',updated_at_ms=?,completed_at_ms=? WHERE id=?", (now_ms, now_ms, job["id"]))
            self._conn.execute("INSERT INTO jobs(id,meeting_id,kind,status,priority,input_transcript_seq,input_version,evidence_segment_id,evidence_hash,generation_id,trigger_type,work_item_id,trigger_reference_seq,user_request,idempotency_key,attempts,max_attempts,next_attempt_at_ms,deadline_at_ms,final_committed_at_ms,created_at_ms,updated_at_ms,request_context_json) VALUES(?,?,'intelligence','pending',90,?,?,?,?,?,'discussion_update',?,?,?,?,0,1,?,?,?,?,?,?)",
                               (job_id, meeting_id, int(anchor["transcript_seq"]), int(paragraph["revision"]), anchor["segment_id"], anchor["evidence_hash"], f"discussion:{row['work_item_id']}:{basis}:{opportunity_id}", job_id, int(anchor["transcript_seq"]), PARTICIPATION_INTENTS["freeform"], f"discussion_update:{meeting_id}:{row['work_item_id']}:{basis}:{opportunity_id}", now_ms, now_ms + 30_000, now_ms, now_ms, now_ms, json.dumps(metadata, ensure_ascii=False)))
            return self._job_dict(self._conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
