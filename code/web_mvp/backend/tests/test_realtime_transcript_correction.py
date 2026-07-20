from meeting_copilot_web_mvp import asr_correct
from meeting_copilot_web_mvp import realtime_transcript_correction as correction


def _final(segment_id: str, text: str, *, at_ms: int = 1_000) -> dict:
    return {
        "id": f"transcript_final:{segment_id}",
        "event_type": "transcript_final",
        "at_ms": at_ms,
        "payload": {
            "segment_id": segment_id,
            "text": text,
            "normalized_text": text,
            "start_ms": max(0, at_ms - 800),
            "end_ms": at_ms,
            "confidence": 0.91,
            "evidence_spans": [
                {
                    "id": f"asr_ev_{segment_id}",
                    "segment_id": segment_id,
                    "quote": text,
                    "start_ms": max(0, at_ms - 800),
                    "end_ms": at_ms,
                    "status": "active",
                }
            ],
        },
    }


def _record(*events: dict) -> dict:
    return {"session_id": "corr_session", "events": list(events)}


def test_policy_contract_constants_are_stable():
    assert correction.POLICY_VERSION == "realtime-transcript-correction.v1"
    assert correction.MIN_BATCH_CHARS == 80
    assert correction.MIN_INTERVAL_MS == 15_000


def test_llm_correction_prompt_requires_index_markers_and_facts_to_survive():
    assert "<<<MC_SEGMENT" in asr_correct._SYSTEM
    assert "负责人" in asr_correct._SYSTEM
    assert "事实" in asr_correct._SYSTEM


def test_partial_events_are_never_eligible():
    batch = correction.eligible_final_batch(
        _record({
            "event_type": "transcript_partial",
            "at_ms": 35_000,
            "payload": {"segment_id": "p1", "text": "正在识别的临时文字" * 30},
        })
    )

    assert batch["eligible"] is False
    assert batch["reason"] == "no_unrevised_final"
    assert batch["final_events"] == []


def test_already_revised_segments_are_skipped():
    final = _final("s1", "这是需要修正的终稿。" * 30)
    revision = {
        "id": "transcript_revision:s1:r1",
        "event_type": "transcript_revision",
        "at_ms": 2_000,
        "payload": {
            "segment_id": "s1_llm_r1",
            "revision_of": "s1",
            "supersedes_segment_id": "s1",
            "text": "这是已经修正的终稿。",
        },
    }

    batch = correction.eligible_final_batch(_record(final, revision), force=True)

    assert batch["eligible"] is False
    assert batch["reason"] == "no_unrevised_final"


def test_short_batch_before_interval_is_deferred():
    record = _record(_final("s1", "短终稿", at_ms=1_000))
    record["realtime_transcript_correction"] = {"waiting_since_epoch_ms": 10_000}
    batch = correction.eligible_final_batch(record, now_ms=24_999)

    assert batch["eligible"] is False
    assert batch["reason"] == "batch_gate_closed"
    assert batch["total_chars"] < correction.MIN_BATCH_CHARS
    assert batch["elapsed_ms"] < correction.MIN_INTERVAL_MS


def test_character_threshold_opens_gate():
    text = "灰度发布需要明确回滚负责人和监控阈值。" * 20
    batch = correction.eligible_final_batch(_record(_final("s1", text)))

    assert batch["eligible"] is True
    assert batch["reason"] == "min_batch_chars_reached"
    assert batch["segment_ids"] == ["s1"]
    assert batch["total_chars"] >= correction.MIN_BATCH_CHARS


def test_elapsed_interval_opens_gate_for_short_final():
    record = _record(_final("s1", "接口先灰度五趴。", at_ms=1_000))
    record["realtime_transcript_correction"] = {"waiting_since_epoch_ms": 10_000}
    batch = correction.eligible_final_batch(record, now_ms=40_000)

    assert batch["eligible"] is True
    assert batch["reason"] == "min_interval_reached"
    assert batch["segment_ids"] == ["s1"]


def test_short_meeting_batch_opens_after_realtime_interval():
    record = _record(_final("s1", "接口先灰度五趴。", at_ms=1_000))
    record["realtime_transcript_correction"] = {"waiting_since_epoch_ms": 10_000}

    batch = correction.eligible_final_batch(record, now_ms=25_000)

    assert batch["eligible"] is True
    assert batch["reason"] == "min_interval_reached"
    assert batch["retry_after_ms"] == 0


def test_first_confirmed_meeting_segment_can_trigger_realtime_correction():
    text = "checkout-service周五晚上灰度百分之十先看error_rate和P99如果指标异常我们暂停扩量但回滚脚本还没有准备好，先确认owner和监控阈值。"
    batch = correction.eligible_final_batch(_record(_final("s1", text)))

    assert batch["eligible"] is True
    assert batch["reason"] == "min_batch_chars_reached"


def test_force_processes_remaining_final_on_stop():
    batch = correction.eligible_final_batch(
        _record(_final("s1", "接口先灰度五趴。")),
        force=True,
    )

    assert batch["eligible"] is True
    assert batch["reason"] == "forced"


def test_force_selects_all_remaining_finals_up_to_hard_batch_limit():
    events = [_final(f"s{index}", f"第{index}段" + "需要校正的中文技术会议内容。" * 8, at_ms=index * 1_000) for index in range(1, 7)]

    batch = correction.eligible_final_batch(_record(*events), force=True)

    assert batch["eligible"] is True
    assert batch["segment_ids"] == [f"s{index}" for index in range(1, 7)]
    assert batch["total_chars"] > correction.MIN_BATCH_CHARS
    assert batch["total_chars"] <= correction.MAX_BATCH_CHARS


def test_single_oversized_final_is_never_sent_past_hard_batch_limit():
    batch = correction.eligible_final_batch(
        _record(_final("oversized", "超" * (correction.MAX_BATCH_CHARS + 1))),
        force=True,
    )

    assert batch["eligible"] is False
    assert batch["reason"] == "oversized_final_skipped"
    assert batch["final_events"] == []
    assert batch["oversized_segment_ids"] == ["oversized"]
    assert batch["total_chars"] == 0


def test_mapping_failed_segment_is_deferred_until_forced_stop_retry():
    record = _record(_final("s1", "接口先灰度五趴。"))
    record["realtime_transcript_correction"] = {"failed_segment_ids": ["s1"]}

    normal = correction.eligible_final_batch(record)
    forced = correction.eligible_final_batch(record, force=True)

    assert normal["eligible"] is False
    assert normal["reason"] == "no_unrevised_final"
    assert forced["eligible"] is True
    assert forced["segment_ids"] == ["s1"]


def test_invalid_corrections_are_rejected():
    final = _final("s1", "我们先灰度百分之五，如果错误率超过百分之零点一就回滚。")
    common = {
        "session_id": "corr_session",
        "final_event": final,
        "source": "fallback_batch",
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }

    assert correction.build_revision_event(corrected_text="", **common) is None
    assert correction.build_revision_event(corrected_text=final["payload"]["text"], **common) is None
    assert correction.build_revision_event(corrected_text="完全无关的另一段内容", **common) is None
    assert correction.build_revision_event(corrected_text="改" * 500, **common) is None


def test_fact_changing_corrections_are_rejected_even_when_text_is_similar():
    usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    owner_final = _final("owner", "负责人是张三，截止时间是周一。")
    ratio_final = _final("ratio", "灰度比例为百分之五。")
    decision_final = _final("decision", "最终结论是通过上线评审。")
    entity_final = _final("entity", "支付服务需要保留回滚开关。")
    implicit_owner_final = _final("implicit_owner", "张三负责回滚演练，明天完成。")
    date_ratio_final = _final("date_ratio", "上线时间是七月十五日，灰度比例是三成。")
    agreement_final = _final("agreement", "最终结论是同意上线。")
    negated_final = _final("negated", "最终结论是没有通过上线评审。")

    assert correction.build_revision_event(
        session_id="corr_session",
        final_event=owner_final,
        corrected_text="负责人是李四，截止时间是周二。",
        source="fallback_batch",
        usage=usage,
    ) is None
    assert correction.build_revision_event(
        session_id="corr_session",
        final_event=ratio_final,
        corrected_text="灰度比例为百分之五十。",
        source="fallback_batch",
        usage=usage,
    ) is None
    assert correction.build_revision_event(
        session_id="corr_session",
        final_event=decision_final,
        corrected_text="最终结论是拒绝上线评审。",
        source="fallback_batch",
        usage=usage,
    ) is None
    assert correction.build_revision_event(
        session_id="corr_session",
        final_event=entity_final,
        corrected_text="订单服务不需要保留回滚开关。",
        source="fallback_batch",
        usage=usage,
    ) is None
    for final_event, corrected_text in (
        (implicit_owner_final, "李四负责回滚演练，后天完成。"),
        (date_ratio_final, "上线时间是七月二十五日，灰度比例是五成。"),
        (agreement_final, "最终结论是否决上线。"),
        (negated_final, "最终结论是已经通过上线评审。"),
    ):
        assert correction.build_revision_event(
            session_id="corr_session",
            final_event=final_event,
            corrected_text=corrected_text,
            source="fallback_batch",
            usage=usage,
        ) is None


def test_chinese_fraction_scales_match_equivalent_percentages_but_reject_changed_values():
    original = "错误率超过千分之一时立即回滚，丢包率超过万分之五时报警。"
    equivalent = "错误率超过 0.1% 时立即回滚，丢包率超过 0.05% 时报警。"
    changed = "错误率超过 0.5% 时立即回滚，丢包率超过 0.05% 时报警。"

    assert correction.correction_is_safe(original, equivalent) is True
    assert correction.correction_is_safe(original, changed) is False


def test_valid_revision_preserves_evidence_and_non_secret_usage():
    final = _final("s1", "我们先灰度百分之五，如果错误率超过百分之零点一就回滚。")
    revision = correction.build_revision_event(
        session_id="corr_session",
        final_event=final,
        corrected_text="我们先灰度 5%，如果错误率超过 0.1% 就回滚。",
        source="combined_suggestion",
        usage={
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "api_key": "must-not-persist",
            "provider_url": "https://secret.example",
        },
    )

    assert revision is not None
    assert revision["event_type"] == "transcript_revision"
    assert revision["payload"]["revision_of"] == "s1"
    assert revision["payload"]["supersedes_segment_id"] == "s1"
    assert revision["payload"]["original_text"] == final["payload"]["text"]
    assert revision["payload"]["evidence_spans"][0]["quote"] == revision["payload"]["text"]
    assert revision["payload"]["superseded_evidence_spans"][0]["status"] == "superseded"
    assert revision["payload"]["correction"]["policy_version"] == correction.POLICY_VERSION
    assert revision["payload"]["correction"]["usage"] == {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
    }
    assert "api_key" not in str(revision)
    assert "secret.example" not in str(revision)


def test_batch_revision_references_batch_id_without_copying_usage():
    final = _final("s1", "我们先灰度百分之五。")

    revision = correction.build_revision_event(
        session_id="corr_session",
        final_event=final,
        corrected_text="我们先灰度 5%。",
        source="fallback_batch",
        usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        batch_id="rtc_batch_123",
    )

    assert revision is not None
    assert revision["payload"]["correction"] == {
        "policy_version": correction.POLICY_VERSION,
        "source": "fallback_batch",
        "batch_id": "rtc_batch_123",
    }


def test_batch_audit_records_safe_provider_and_execution_metadata():
    record = correction.begin_reservation(
        _record(_final("s1", "我们先灰度百分之五。")),
        batch_id="rtc_batch_123",
        segment_ids=["s1"],
        now_ms=1_000,
        retry_count=1,
    )

    updated = correction.commit_batch_audit(
        record,
        batch_id="rtc_batch_123",
        status="provider_failed",
        completed_at_ms=2_000,
        usage={"total_tokens": 3, "provider_url": "https://secret.example", "api_key": "sk-secret"},
        error_code="llm_provider_failed",
        provider="team_gateway",
        model="gpt-5.5",
        purpose="realtime_transcript_correction",
        degraded=True,
        fallback=True,
        retry=True,
    )

    audit = updated["realtime_transcript_correction"]["batch_audits"][0]
    assert audit == {
        "batch_id": "rtc_batch_123",
        "segment_ids": ["s1"],
        "started_at_ms": 1_000,
        "completed_at_ms": 2_000,
        "retry_count": 1,
        "status": "provider_failed",
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 3},
        "error_code": "llm_provider_failed",
        "provider": "team_gateway",
        "model": "gpt-5.5",
        "purpose": "realtime_transcript_correction",
        "degraded": True,
        "fallback": True,
        "retry": True,
    }
    assert "secret.example" not in str(audit)
    assert "sk-secret" not in str(audit)


def test_expired_reservation_allows_only_one_controlled_retry():
    record = _record(_final("s1", "我们先灰度百分之五。"))
    record["realtime_transcript_correction"] = {
        "reservation": {
            "batch_id": "rtc_batch_123",
            "segment_ids": ["s1"],
            "started_at_ms": 1_000,
            "lease_expires_at_ms": 2_000,
            "retry_count": 0,
            "status": "reserved",
        }
    }

    retry = correction.reservation_action(record, now_ms=2_001)
    record["realtime_transcript_correction"]["reservation"]["retry_count"] = 1
    terminal = correction.reservation_action(record, now_ms=2_001)

    assert retry["action"] == "retry"
    assert retry["reservation"]["retry_count"] == 0
    assert terminal["action"] == "expired_terminal"


def test_apply_revision_events_is_idempotent_and_persists_status():
    final = _final("s1", "我们先灰度百分之五。")
    revision = correction.build_revision_event(
        session_id="corr_session",
        final_event=final,
        corrected_text="我们先灰度 5%。",
        source="fallback_batch",
        usage={"total_tokens": 7},
    )
    assert revision is not None

    first = correction.apply_revision_events(
        _record(final),
        [revision],
        status={"status": "completed", "last_triggered_at_ms": 31_000},
    )
    second = correction.apply_revision_events(
        first,
        [revision],
        status={"status": "completed", "last_triggered_at_ms": 31_000},
    )

    revisions = [event for event in second["events"] if event.get("event_type") == "transcript_revision"]
    assert len(revisions) == 1
    assert second["realtime_transcript_correction"]["policy_version"] == correction.POLICY_VERSION
    assert second["realtime_transcript_correction"]["revised_segment_ids"] == ["s1"]


def test_apply_revision_events_does_not_deepcopy_unchanged_history():
    final = _final("s1", "我们先灰度百分之五。")
    record = _record(final)

    updated = correction.apply_revision_events(record, [], status={"status": "waiting"})

    assert updated is not record
    assert updated["events"] is not record["events"]
    assert updated["events"][0] is final
