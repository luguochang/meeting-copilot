from __future__ import annotations

import pytest

from meeting_copilot_web_mvp.v2_persistence import V2Persistence


def test_notes_composite_cursor_paginates_thousand_same_timestamp_rows(tmp_path):
    repository = V2Persistence(tmp_path / "notes.db")
    try:
        with repository._write_transaction():
            repository._conn.executemany("INSERT INTO meeting_notes(note_id,title,body,source_kind,version,status,created_at_ms,updated_at_ms) "
                                         "VALUES(?,?,?,'manual',1,'active',?,?)",
                                         [(f"note:{number:04}", f"项目资料{number}", "本地文档", 100, 200 if number % 2 else 100) for number in range(1001)])
        seen, cursor = [], None
        while True:
            page = repository.list_notes_page(limit=113, cursor=cursor)
            seen.extend(note["note_id"] for note in page["items"])
            if not page["has_more"]:
                assert page["next_cursor"] is None
                break
            cursor = page["next_cursor"]
        expected = [row[0] for row in repository._conn.execute("SELECT note_id FROM meeting_notes ORDER BY updated_at_ms DESC,note_id DESC")]
        assert seen == expected and len(seen) == len(set(seen)) == 1001
        assert len(repository.list_notes(limit=100)["notes"]) == 100
        assert len(repository.list_notes_page(query="项目资料1000")["items"]) == 1
        assert repository.list_notes_page(query="%") == {"items": [], "has_more": False, "next_cursor": None}
    finally:
        repository.close()


@pytest.mark.parametrize("cursor", [{"id": "x", "updated_at_ms": True}, {"id": "", "updated_at_ms": 1},
    {"id": "x", "updated_at_ms": -1}, {"id": "x", "updated_at_ms": "1"}, {"id": "x", "updated_at_ms": 2**63},
    {"id": "x", "updated_at_ms": 1, "extra": "x"}, {"id": "x\n", "updated_at_ms": 1}])
def test_malformed_compound_note_cursor_rejected(tmp_path, cursor):
    repository = V2Persistence(tmp_path / "notes.db")
    try:
        with pytest.raises(ValueError, match="cursor"):
            repository.list_notes_page(cursor=cursor)
    finally:
        repository.close()
