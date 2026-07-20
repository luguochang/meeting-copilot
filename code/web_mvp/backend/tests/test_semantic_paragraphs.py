from __future__ import annotations

from meeting_copilot_web_mvp.semantic_paragraphs import (
    ParagraphAssembler,
    RealtimeIntelligenceBatcher,
)


def _checkpoint(
    checkpoint_id: str,
    text: str,
    start_ms: int,
    end_ms: int,
    *,
    speaker: str = "speaker-1",
) -> dict:
    return {
        "checkpoint_id": checkpoint_id,
        "text": text,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "speaker": speaker,
    }


def test_forced_fifteen_second_checkpoints_form_one_stable_paragraph() -> None:
    assembler = ParagraphAssembler(stabilize_silence_ms=1_800, maximum_paragraph_ms=60_000)

    assembler.add(_checkpoint("cp-1", "我们先把支付接口的超时", 0, 15_000))
    assembler.add(_checkpoint("cp-2", "阈值改成三秒，然后观察错误率", 15_050, 30_000))
    assembler.add(_checkpoint("cp-3", "超过千分之一就回滚。", 30_050, 42_000))

    snapshot = assembler.snapshot()
    assert snapshot["stable_paragraphs"] == []
    assert snapshot["active_paragraph"]["text"] == (
        "我们先把支付接口的超时阈值改成三秒，然后观察错误率超过千分之一就回滚。"
    )
    assert snapshot["active_paragraph"]["checkpoint_ids"] == ["cp-1", "cp-2", "cp-3"]

    stable = assembler.flush(now_ms=44_000)
    assert stable is not None
    assert stable["id"] == "paragraph:cp-1"
    assert stable["text"] == snapshot["active_paragraph"]["text"]
    assert assembler.snapshot()["active_paragraph"] is None


def test_repeated_checkpoint_updates_in_place_without_duplicate_text() -> None:
    assembler = ParagraphAssembler()

    assembler.add(_checkpoint("cp-1", "支付服务先灰度百分", 0, 10_000))
    assembler.add(_checkpoint("cp-1", "支付服务先灰度百分之五。", 0, 12_000))

    snapshot = assembler.snapshot()
    assert snapshot["active_paragraph"]["text"] == "支付服务先灰度百分之五。"
    assert snapshot["active_paragraph"]["checkpoint_ids"] == ["cp-1"]


def test_speaker_change_stabilizes_previous_paragraph() -> None:
    assembler = ParagraphAssembler()
    assembler.add(_checkpoint("cp-1", "先说明发布计划。", 0, 4_000, speaker="speaker-1"))

    emitted = assembler.add(
        _checkpoint("cp-2", "我来补充回滚方案。", 4_100, 8_000, speaker="speaker-2")
    )

    assert [item["id"] for item in emitted] == ["paragraph:cp-1"]
    snapshot = assembler.snapshot()
    assert snapshot["stable_paragraphs"][0]["speaker"] == "speaker-1"
    assert snapshot["active_paragraph"]["speaker"] == "speaker-2"


def test_long_silence_stabilizes_previous_paragraph_before_new_content() -> None:
    assembler = ParagraphAssembler(stabilize_silence_ms=1_800)
    assembler.add(_checkpoint("cp-1", "第一段已经说完。", 0, 4_000))

    emitted = assembler.add(_checkpoint("cp-2", "现在进入第二个话题。", 6_500, 10_000))

    assert [item["id"] for item in emitted] == ["paragraph:cp-1"]
    assert assembler.snapshot()["active_paragraph"]["id"] == "paragraph:cp-2"


def test_batcher_debounces_new_paragraphs_and_has_a_maximum_wait() -> None:
    batcher = RealtimeIntelligenceBatcher(debounce_ms=2_000, maximum_wait_ms=7_000)
    batcher.offer("paragraph-1", stable_at_ms=1_000)
    assert batcher.claim_ready(now_ms=2_999) is None

    batcher.offer("paragraph-2", stable_at_ms=2_900)
    assert batcher.claim_ready(now_ms=4_899) is None
    assert batcher.claim_ready(now_ms=4_900) == ("paragraph-1", "paragraph-2")

    batcher.complete()
    batcher.offer("paragraph-3", stable_at_ms=10_000)
    batcher.offer("paragraph-4", stable_at_ms=16_500)
    assert batcher.claim_ready(now_ms=16_999) is None
    assert batcher.claim_ready(now_ms=17_000) == ("paragraph-3", "paragraph-4")


def test_batcher_is_single_flight_and_coalesces_arrivals_while_running() -> None:
    batcher = RealtimeIntelligenceBatcher(debounce_ms=1_500, maximum_wait_ms=6_000)
    batcher.offer("paragraph-1", stable_at_ms=0)
    assert batcher.claim_ready(now_ms=1_500) == ("paragraph-1",)

    batcher.offer("paragraph-2", stable_at_ms=1_600)
    batcher.offer("paragraph-3", stable_at_ms=1_700)
    assert batcher.claim_ready(now_ms=9_000) is None

    batcher.complete()
    assert batcher.claim_ready(now_ms=9_000) == ("paragraph-2", "paragraph-3")


def test_batcher_deduplicates_same_paragraph_identity() -> None:
    batcher = RealtimeIntelligenceBatcher()
    batcher.offer("paragraph-1", stable_at_ms=100)
    batcher.offer("paragraph-1", stable_at_ms=200)

    assert batcher.pending_count == 1
