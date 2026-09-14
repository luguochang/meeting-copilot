import pytest

from meeting_copilot_web_mvp.pi_evidence_registry import PiEvidenceRegistry
from meeting_copilot_web_mvp.v2_persistence import V2Persistence


@pytest.fixture
def store(tmp_path):
    instance = V2Persistence(tmp_path / "evidence.db")
    for meeting in ("current", "other"):
        for index in range(12):
            text = "Release requires legal approval." if index == 0 else f"Routine update {index}."
            instance.commit_final_and_enqueue(
                meeting_id=meeting, final_id=f"{meeting}-f-{index}",
                segment_id=f"{meeting}-s-{index}", text=text, normalized_text=text,
                started_at_ms=index * 1000, ended_at_ms=index * 1000 + 900,
                evidence_hash=f"{meeting}-hash-{index}", now_ms=index * 1000 + 1000,
            )
    yield instance
    instance.close()


def test_search_reaches_beyond_recent_eight_without_crossing_meetings(store):
    registry = PiEvidenceRegistry(store, meeting_id="current")
    found = registry.search("legal approval")
    assert [item["segment_id"] for item in found] == ["current-s-0"]
    assert found[0]["evidence_hash"] == "current-hash-0"
    assert registry.validate({"current-s-0": "legal approval"}) == found
    with pytest.raises(ValueError, match="unregistered"):
        registry.validate({"other-s-0": "legal approval"})


def test_query_is_literal_bounded_and_host_scoped(store):
    registry = PiEvidenceRegistry(store, meeting_id="current")
    assert len(registry.search("Routine")) == 6
    assert registry.search("%' OR 1=1 --") == []
    with pytest.raises(ValueError):
        registry.search("Routine", limit=7)
    registry.search("Routine", limit=1)
    with pytest.raises(ValueError, match="budget"):
        registry.search("Routine")


def test_search_can_register_a_bounded_same_meeting_neighborhood(store):
    registry = PiEvidenceRegistry(store, meeting_id="current")
    found = registry.search("legal approval", include_neighbors=True)
    assert [item["segment_id"] for item in found] == ["current-s-0", "current-s-1"]
    assert found[1]["evidence_relation"] == "neighbor"
    validated = registry.validate({
        "current-s-0": "Release requires legal approval.",
        "current-s-1": "Routine update 1.",
    })
    assert {item["segment_id"] for item in validated} == {"current-s-0", "current-s-1"}


def test_read_span_returns_exact_current_segment_and_bounded_neighbors(store):
    registry = PiEvidenceRegistry(store, meeting_id="current")
    span = registry.read_span("current-s-5", before=2, after=2)
    assert [item["segment_id"] for item in span] == [
        "current-s-3", "current-s-4", "current-s-5", "current-s-6", "current-s-7",
    ]
    assert span[2].get("evidence_relation") is None
    assert span[0]["evidence_relation"] == "neighbor"
    assert registry.validate({"current-s-5": "Routine update 5."})[0]["segment_id"] == "current-s-5"


def test_read_span_is_live_meeting_scoped_and_revision_safe(store):
    registry = PiEvidenceRegistry(store, meeting_id="current")
    registry.read_span("current-s-0")
    store.commit_transcript_revision(
        meeting_id="current", segment_id="current-s-0",
        expected_evidence_hash="current-hash-0", corrected_text="Approval is now confirmed.",
        revision_id="span-corrected", now_ms=20000,
    )
    with pytest.raises(ValueError, match="changed"):
        registry.validate({"current-s-0": "Release requires legal approval."})
    other = PiEvidenceRegistry(store, meeting_id="other")
    with pytest.raises(KeyError):
        other.read_span("current-s-0")
    store.end_meeting(meeting_id="current", now_ms=21000)
    with pytest.raises(ValueError, match="live meeting"):
        registry.read_span("current-s-1")


def test_read_span_shares_the_four_call_search_budget(store):
    registry = PiEvidenceRegistry(store, meeting_id="current")
    for index in range(4):
        registry.read_span(f"current-s-{index}", before=0, after=0)
    with pytest.raises(ValueError, match="budget"):
        registry.read_span("current-s-4", before=0, after=0)


def test_registry_does_not_trust_mutated_tool_result_or_forged_quote(store):
    registry = PiEvidenceRegistry(store, meeting_id="current")
    found = registry.search("legal approval")
    found[0]["normalized_text"] = "Approval granted."
    with pytest.raises(ValueError, match="verbatim"):
        registry.validate({"current-s-0": "Approval granted."})
    assert registry.validate({"current-s-0": "legal approval"})[0]["normalized_text"] != found[0]["normalized_text"]
    other_run = PiEvidenceRegistry(store, meeting_id="current")
    with pytest.raises(ValueError, match="unregistered"):
        other_run.validate({"current-s-0": "legal approval"})


def test_revision_invalidates_registered_evidence(store):
    registry = PiEvidenceRegistry(store, meeting_id="current")
    registry.search("legal approval")
    store.commit_transcript_revision(
        meeting_id="current", segment_id="current-s-0",
        expected_evidence_hash="current-hash-0", corrected_text="Release no longer requires legal approval.",
        revision_id="corrected", now_ms=20000,
    )
    with pytest.raises(ValueError, match="changed"):
        registry.validate({"current-s-0": "legal approval"})


def test_closed_meeting_rejects_search_and_registered_result(store):
    registry = PiEvidenceRegistry(store, meeting_id="current")
    registry.search("legal approval")
    store.end_meeting(meeting_id="current", now_ms=20000)
    with pytest.raises(ValueError, match="live meeting"):
        registry.search("legal approval")
    with pytest.raises(ValueError, match="live meeting"):
        registry.validate({"current-s-0": "legal approval"})
