import hashlib
import json

from tools.long_soak_boundary_counts import boundary_counts


MEETING = "long_audio_soak_20261008_r02"


def entry(event, **fields):
    return json.dumps({"event": event, "session_id": MEETING, **fields})


def test_timeout_attempts_and_recovery_are_counted_independently():
    prefix = "asr.stream.funasr_boundary_"
    text = "\n".join([
        entry(prefix + "timeout", boundary_id="utterance-boundary-1", recovering=False),
        entry(prefix + "timeout", boundary_id="utterance-boundary-1", recovering=False),
        entry(prefix + "timeout", boundary_id="utterance-boundary-2", recovering=True),
        entry(prefix + "recovered", boundary_id="utterance-boundary-1"),
        entry(prefix + "failed", boundary_id="utterance-boundary-3"),
        entry("asr.stream.aborted"),
    ])
    result = boundary_counts(text, meeting_id=MEETING)
    assert result["ordinary_ack_timeout_attempts"] == 2
    assert result["recovery_ack_timeout_attempts"] == 1
    assert result["ack_timeout_distinct_boundary_count"] == 2
    assert result["observed_explicit_recovery_success_event_count"] == 1
    assert result["total_successful_timeout_resolution_count"] is None
    assert result["boundary_failed_event_count"] == 1
    assert result["stream_terminated_event_count"] == 1


def test_production_identifier_hash_matches_without_returning_content():
    meeting_hash = hashlib.sha256(MEETING.encode()).hexdigest()[:16]
    event = "asr.stream.funasr_boundary_timeout"
    log = json.dumps({"event": event, "meeting_id_hash": meeting_hash,
                      "boundary_id": "utterance-boundary-61", "recovering": True,
                      "private_body": "PRIVATE_ASR_TEXT", "authorization": "PRIVATE_KEY"})
    result = boundary_counts("prefix " + log, meeting_id=MEETING)
    assert result["recovery_ack_timeout_attempts"] == 1
    assert result["ack_timeout_distinct_boundary_count"] == 1
    serialized = json.dumps(result)
    for value in ["PRIVATE_ASR_TEXT", "PRIVATE_KEY", meeting_hash, "utterance-boundary-61"]:
        assert value not in serialized


def test_unrelated_malformed_and_unscoped_records_do_not_count_as_success():
    event = "asr.stream.funasr_boundary_timeout"
    result = boundary_counts("\n".join([
        json.dumps({"event": event, "session_id": "some-other-meeting", "recovering": True}),
        json.dumps({"event": event, "private_body": "PRIVATE_ASR_TEXT"}),
        "prefix " + event + " {malformed PRIVATE_KEY",
        json.dumps({"event": "unrelated", "session_id": MEETING, "body": event}),
    ]), meeting_id=MEETING)
    assert result["ordinary_ack_timeout_attempts"] == 0
    assert result["recovery_ack_timeout_attempts"] == 0
    assert result["observed_explicit_recovery_success_event_count"] == 0
    assert result["total_successful_timeout_resolution_count"] is None
    assert result["matching_malformed_record_count"] == 1
    assert result["matching_unscoped_record_count"] == 1
    assert "PRIVATE" not in json.dumps(result)
